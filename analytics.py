import atexit
import sqlite3
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

from settings import DATABASE

BUFFER_LOCK = threading.Lock()
PAGE_VISIT_BUFFER = {}
VIDEO_VIEW_BUFFER = {}
VIDEO_WATCH_BUFFER = {}

FLUSH_INTERVAL_SECONDS = 10
FLUSH_EVENT_THRESHOLD = 100
WATCH_BUCKET_SECONDS = 10

_FLUSH_THREAD = None
_STOP_EVENT = threading.Event()

ADMIN_ANALYTICS_PREFIXES = (
    "/admin",
)
ADMIN_ANALYTICS_EXACT_PATHS = {
    "/upload",
    "/create_collection",
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _buffer_size():
    return len(PAGE_VISIT_BUFFER) + len(VIDEO_VIEW_BUFFER) + len(VIDEO_WATCH_BUFFER)


def _normalize_path(path):
    raw = (path or "/").strip() or "/"
    parsed = urlsplit(raw)
    normalized = (parsed.path or "/").strip() or "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _is_admin_analytics_path(path):
    normalized = path.rstrip("/") or "/"

    if normalized in ADMIN_ANALYTICS_EXACT_PATHS:
        return True

    for prefix in ADMIN_ANALYTICS_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True

    return False


def record_page_visit(path):
    normalized = _normalize_path(path)
    if _is_admin_analytics_path(normalized):
        return

    with BUFFER_LOCK:
        PAGE_VISIT_BUFFER[normalized] = PAGE_VISIT_BUFFER.get(normalized, 0) + 1
        should_flush = _buffer_size() >= FLUSH_EVENT_THRESHOLD

    if should_flush:
        flush_to_db()


def record_video_view(video_id):
    if not video_id:
        return

    with BUFFER_LOCK:
        key = str(video_id)
        VIDEO_VIEW_BUFFER[key] = VIDEO_VIEW_BUFFER.get(key, 0) + 1
        should_flush = _buffer_size() >= FLUSH_EVENT_THRESHOLD

    if should_flush:
        flush_to_db()


def record_video_watch(video_id, current_time, delta_seconds):
    if not video_id:
        return

    try:
        delta = float(delta_seconds or 0)
    except (TypeError, ValueError):
        delta = 0

    if delta <= 0:
        return

    try:
        current = float(current_time or 0)
    except (TypeError, ValueError):
        current = 0

    if current < 0:
        current = 0

    bucket_start = int(current // WATCH_BUCKET_SECONDS) * WATCH_BUCKET_SECONDS

    with BUFFER_LOCK:
        key = (str(video_id), bucket_start)
        VIDEO_WATCH_BUFFER[key] = VIDEO_WATCH_BUFFER.get(key, 0.0) + delta
        should_flush = _buffer_size() >= FLUSH_EVENT_THRESHOLD

    if should_flush:
        flush_to_db()


def flush_to_db():
    with BUFFER_LOCK:
        if not PAGE_VISIT_BUFFER and not VIDEO_VIEW_BUFFER and not VIDEO_WATCH_BUFFER:
            return

        page_snapshot = PAGE_VISIT_BUFFER.copy()
        view_snapshot = VIDEO_VIEW_BUFFER.copy()
        watch_snapshot = VIDEO_WATCH_BUFFER.copy()

        PAGE_VISIT_BUFFER.clear()
        VIDEO_VIEW_BUFFER.clear()
        VIDEO_WATCH_BUFFER.clear()

    now = _now_iso()
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")

    try:
        for path, count in page_snapshot.items():
            conn.execute(
                """
                INSERT INTO page_visits (path, visit_count, last_visited_at)
                VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    visit_count = page_visits.visit_count + excluded.visit_count,
                    last_visited_at = excluded.last_visited_at
                """,
                (path, int(count), now),
            )

        for video_id, count in view_snapshot.items():
            conn.execute(
                """
                INSERT INTO video_views (video_id, view_count, last_viewed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    view_count = video_views.view_count + excluded.view_count,
                    last_viewed_at = excluded.last_viewed_at
                """,
                (video_id, int(count), now),
            )

        for (video_id, bucket_start), watch_seconds in watch_snapshot.items():
            conn.execute(
                """
                INSERT INTO video_watch_buckets (video_id, bucket_start_sec, watch_seconds, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(video_id, bucket_start_sec) DO UPDATE SET
                    watch_seconds = video_watch_buckets.watch_seconds + excluded.watch_seconds,
                    updated_at = excluded.updated_at
                """,
                (video_id, int(bucket_start), float(watch_seconds), now),
            )

        conn.commit()
    finally:
        conn.close()


def start_analytics_flusher():
    global _FLUSH_THREAD

    if _FLUSH_THREAD and _FLUSH_THREAD.is_alive():
        return

    _STOP_EVENT.clear()

    def loop():
        while not _STOP_EVENT.wait(FLUSH_INTERVAL_SECONDS):
            flush_to_db()

    _FLUSH_THREAD = threading.Thread(target=loop, daemon=True, name="analytics-flush")
    _FLUSH_THREAD.start()


def stop_analytics_flusher():
    _STOP_EVENT.set()
    flush_to_db()


def get_analytics_dashboard(limit=20):
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")

    top_pages = conn.execute(
        """
        SELECT path, visit_count, last_visited_at
        FROM page_visits
        ORDER BY visit_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    top_videos = conn.execute(
        """
        SELECT v.id,
               COALESCE(v.display_name, v.filename) AS title,
               vv.view_count,
               vv.last_viewed_at
        FROM video_views vv
        JOIN videos v ON v.id = vv.video_id
        ORDER BY vv.view_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    top_segments = conn.execute(
        """
        SELECT v.id,
               COALESCE(v.display_name, v.filename) AS title,
               b.bucket_start_sec,
               b.watch_seconds
        FROM video_watch_buckets b
        JOIN videos v ON v.id = b.video_id
        ORDER BY b.watch_seconds DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    return {
        "top_pages": top_pages,
        "top_videos": top_videos,
        "top_segments": top_segments,
    }


atexit.register(stop_analytics_flusher)
