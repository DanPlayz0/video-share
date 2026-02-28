import os
import queue
import sqlite3
import subprocess
import threading
import time

from settings import DATABASE, HLS_FOLDER, HLS_MAX_CONCURRENT_STREAMS

HLS_RUNTIME_PROGRESS = {}
HLS_RUNTIME_LOCK = threading.Lock()
HLS_JOB_QUEUE = queue.Queue()
HLS_QUEUE_LOCK = threading.Lock()
HLS_QUEUED_VIDEO_IDS = set()
HLS_WORKERS_STARTED = False


def probe_duration_seconds(input_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        value = (result.stdout or "").strip()
        if not value:
            return 0
        seconds = int(float(value))
        return max(seconds, 0)
    except Exception:
        return 0


def inspect_hls_state(video_id):
    output_dir = os.path.join(HLS_FOLDER, video_id)
    playlist_path = os.path.join(output_dir, "playlist.m3u8")

    if not os.path.isdir(output_dir):
        return {
            "status": "missing",
            "segments_generated": 0,
            "segments_expected": 0,
        }

    generated_segments = sum(
        1 for name in os.listdir(output_dir)
        if name.lower().endswith(".ts")
    )

    if not os.path.exists(playlist_path):
        status = "processing" if generated_segments > 0 else "missing"
        return {
            "status": status,
            "segments_generated": generated_segments,
            "segments_expected": 0,
        }

    try:
        with open(playlist_path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = [line.strip() for line in handle.readlines()]
    except OSError:
        return {
            "status": "processing",
            "segments_generated": generated_segments,
            "segments_expected": 0,
        }

    expected_segments = sum(
        1 for line in lines
        if line and not line.startswith("#") and line.lower().endswith(".ts")
    )
    has_endlist = any(line == "#EXT-X-ENDLIST" for line in lines)

    if expected_segments > 0 and has_endlist and generated_segments >= expected_segments:
        status = "complete"
    elif generated_segments > 0 or expected_segments > 0:
        status = "processing"
    else:
        status = "missing"

    return {
        "status": status,
        "segments_generated": generated_segments,
        "segments_expected": expected_segments,
    }


def _update_hls_metadata(video_id, **fields):
    if not fields:
        return

    assignments = ", ".join(f"{key} = ?" for key in fields.keys())
    values = list(fields.values()) + [video_id]

    for attempt in range(5):
        conn = sqlite3.connect(DATABASE, timeout=10)
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            conn.execute(f"UPDATE videos SET {assignments} WHERE id = ?", values)
            conn.commit()
            conn.close()
            return
        except sqlite3.OperationalError as exc:
            conn.close()
            if "locked" not in str(exc).lower() or attempt == 4:
                raise
            time.sleep(0.2 * (2 ** attempt))


def _set_runtime_progress(video_id, payload):
    with HLS_RUNTIME_LOCK:
        current = HLS_RUNTIME_PROGRESS.get(video_id, {})
        current.update(payload)
        HLS_RUNTIME_PROGRESS[video_id] = current


def get_runtime_hls_progress(video_id):
    with HLS_RUNTIME_LOCK:
        item = HLS_RUNTIME_PROGRESS.get(video_id)
        return dict(item) if item else None


def clear_runtime_hls_progress(video_id):
    with HLS_RUNTIME_LOCK:
        HLS_RUNTIME_PROGRESS.pop(video_id, None)


def _run_hls_encode(video_id, input_path, duration_seconds=0):
    output_dir = os.path.join(HLS_FOLDER, video_id)
    os.makedirs(output_dir, exist_ok=True)
    playlist_path = os.path.join(output_dir, "playlist.m3u8")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-f", "hls",
        "-progress", "pipe:1",
        "-nostats",
        "-hls_time", "6",
        "-hls_playlist_type", "vod",
        "-hls_segment_filename",
        os.path.join(output_dir, "%03d.ts"),
        playlist_path,
    ]

    _set_runtime_progress(
        video_id,
        {
            "status": "processing",
            "progress_pct": 0,
            "step": "starting",
            "error": "",
        },
    )
    _update_hls_metadata(
        video_id,
        hls_status="processing",
        hls_progress_pct=0,
        hls_step="starting",
        hls_error=None,
    )

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        _set_runtime_progress(
            video_id,
            {
                "status": "failed",
                "step": "error",
                "error": str(exc),
            },
        )
        _update_hls_metadata(
            video_id,
            hls_status="failed",
            hls_step="error",
            hls_error=str(exc),
        )
        return

    last_progress = 0

    if process.stdout:
        for raw_line in process.stdout:
            line = (raw_line or "").strip()
            if not line or "=" not in line:
                continue

            key, value = line.split("=", 1)
            if key == "out_time_ms":
                try:
                    out_seconds = int(value) / 1_000_000
                except ValueError:
                    continue

                if duration_seconds and duration_seconds > 0:
                    computed = int((out_seconds / duration_seconds) * 100)
                    next_progress = min(99, max(0, computed))
                    if next_progress != last_progress:
                        last_progress = next_progress
                        hls_state_live = inspect_hls_state(video_id)
                        _set_runtime_progress(
                            video_id,
                            {
                                "status": "processing",
                                "progress_pct": last_progress,
                                "step": "encoding",
                                "segments_generated": hls_state_live["segments_generated"],
                                "segments_expected": hls_state_live["segments_expected"],
                            },
                        )
            elif key == "progress" and value == "end":
                break

    return_code = process.wait()
    hls_state = inspect_hls_state(video_id)

    if return_code == 0 and hls_state["status"] == "complete":
        _set_runtime_progress(
            video_id,
            {
                "status": "complete",
                "progress_pct": 100,
                "step": "done",
                "error": "",
                "segments_generated": hls_state["segments_generated"],
                "segments_expected": hls_state["segments_expected"],
            },
        )
        _update_hls_metadata(
            video_id,
            hls_status="complete",
            hls_progress_pct=100,
            hls_step="done",
            hls_error=None,
            hls_segments_generated=hls_state["segments_generated"],
            hls_segments_expected=hls_state["segments_expected"],
        )
        return

    if return_code == 0:
        _set_runtime_progress(
            video_id,
            {
                "status": "processing",
                "progress_pct": max(last_progress, 1),
                "step": "finalizing",
                "error": "",
                "segments_generated": hls_state["segments_generated"],
                "segments_expected": hls_state["segments_expected"],
            },
        )
        _update_hls_metadata(
            video_id,
            hls_status="processing",
            hls_progress_pct=max(last_progress, 1),
            hls_step="finalizing",
            hls_error=None,
            hls_segments_generated=hls_state["segments_generated"],
            hls_segments_expected=hls_state["segments_expected"],
        )
        return

    _set_runtime_progress(
        video_id,
        {
            "status": "failed",
            "step": "error",
            "error": f"ffmpeg exited with code {return_code}",
            "segments_generated": hls_state["segments_generated"],
            "segments_expected": hls_state["segments_expected"],
        },
    )
    _update_hls_metadata(
        video_id,
        hls_status="failed",
        hls_step="error",
        hls_error=f"ffmpeg exited with code {return_code}",
        hls_segments_generated=hls_state["segments_generated"],
        hls_segments_expected=hls_state["segments_expected"],
    )


def _hls_worker_loop():
    while True:
        video_id, input_path, duration_seconds = HLS_JOB_QUEUE.get()
        try:
            _run_hls_encode(video_id, input_path, duration_seconds=duration_seconds)
        finally:
            with HLS_QUEUE_LOCK:
                HLS_QUEUED_VIDEO_IDS.discard(video_id)
            HLS_JOB_QUEUE.task_done()


def _ensure_hls_workers_started():
    global HLS_WORKERS_STARTED

    with HLS_QUEUE_LOCK:
        if HLS_WORKERS_STARTED:
            return

        for idx in range(max(1, HLS_MAX_CONCURRENT_STREAMS)):
            worker = threading.Thread(
                target=_hls_worker_loop,
                daemon=True,
                name=f"hls-worker-{idx + 1}",
            )
            worker.start()

        HLS_WORKERS_STARTED = True


def convert_to_hls(video_id, input_path, duration_seconds=0):
    existing_runtime = get_runtime_hls_progress(video_id)
    if existing_runtime and existing_runtime.get("status") == "processing":
        return

    with HLS_QUEUE_LOCK:
        if video_id in HLS_QUEUED_VIDEO_IDS:
            return
        HLS_QUEUED_VIDEO_IDS.add(video_id)

    _ensure_hls_workers_started()

    _update_hls_metadata(
        video_id,
        hls_status="processing",
        hls_progress_pct=0,
        hls_step="queued",
        hls_error=None,
    )

    _set_runtime_progress(
        video_id,
        {
            "status": "processing",
            "progress_pct": 0,
            "step": "queued",
            "error": "",
            "segments_generated": 0,
            "segments_expected": 0,
        },
    )

    HLS_JOB_QUEUE.put((video_id, input_path, duration_seconds))
