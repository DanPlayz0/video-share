import sqlite3

from settings import DATABASE


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS collections (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        slug TEXT NOT NULL,
        parent_id TEXT,
        visibility TEXT NOT NULL DEFAULT 'public',
        UNIQUE(slug, parent_id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS videos (
        id TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        display_name TEXT,
        duration_seconds INTEGER NOT NULL DEFAULT 0,
        hls_status TEXT NOT NULL DEFAULT 'pending',
        hls_progress_pct INTEGER NOT NULL DEFAULT 0,
        hls_step TEXT NOT NULL DEFAULT 'pending',
        hls_error TEXT,
        hls_segments_generated INTEGER NOT NULL DEFAULT 0,
        hls_segments_expected INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0,
        visibility TEXT NOT NULL DEFAULT 'public',
        collection_id TEXT,
        FOREIGN KEY(collection_id) REFERENCES collections(id)
    )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_collections_parent_id ON collections(parent_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_videos_collection_order ON videos(collection_id, sort_order)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_videos_collection_visibility ON videos(collection_id, visibility)")

    columns = {
        row["name"] for row in c.execute("PRAGMA table_info(videos)").fetchall()
    }
    if "display_name" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN display_name TEXT")
    if "duration_seconds" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN duration_seconds INTEGER NOT NULL DEFAULT 0")
    if "hls_status" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN hls_status TEXT NOT NULL DEFAULT 'pending'")
    if "hls_progress_pct" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN hls_progress_pct INTEGER NOT NULL DEFAULT 0")
    if "hls_step" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN hls_step TEXT NOT NULL DEFAULT 'pending'")
    if "hls_error" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN hls_error TEXT")
    if "hls_segments_generated" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN hls_segments_generated INTEGER NOT NULL DEFAULT 0")
    if "hls_segments_expected" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN hls_segments_expected INTEGER NOT NULL DEFAULT 0")
    if "sort_order" not in columns:
        c.execute("ALTER TABLE videos ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")

    c.execute(
        "UPDATE videos SET display_name = filename WHERE display_name IS NULL OR TRIM(display_name) = ''"
    )
    c.execute("UPDATE videos SET hls_status = 'pending' WHERE hls_status IS NULL OR TRIM(hls_status) = ''")
    c.execute("UPDATE videos SET hls_step = 'pending' WHERE hls_step IS NULL OR TRIM(hls_step) = ''")

    conn.commit()
    conn.close()


def get_collection_parent_options(conn):
    rows = conn.execute(
        "SELECT id, name, slug, parent_id FROM collections"
    ).fetchall()

    by_parent = {}
    for row in rows:
        parent_key = row["parent_id"]
        by_parent.setdefault(parent_key, []).append(row)

    for children in by_parent.values():
        children.sort(key=lambda item: item["name"].lower())

    options = []

    def walk(parent_id=None, depth=0, slug_parts=None):
        if slug_parts is None:
            slug_parts = []

        for item in by_parent.get(parent_id, []):
            current_slug_parts = [*slug_parts, item["slug"]]
            options.append({
                "id": item["id"],
                "label": f"{'— ' * depth}{item['name']}",
                "path": "/".join(current_slug_parts),
            })
            walk(item["id"], depth + 1, current_slug_parts)

    walk()
    return options
