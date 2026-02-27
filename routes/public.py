import os

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_from_directory, session

from analytics import record_page_visit, record_video_view, record_video_watch
from db import get_collection_parent_options, get_db
from settings import HLS_FOLDER

public_bp = Blueprint("public", __name__)


def get_descendant_ids(conn, root_id):
    rows = conn.execute("SELECT id, parent_id FROM collections").fetchall()
    by_parent = {}
    for row in rows:
        by_parent.setdefault(row["parent_id"], []).append(row["id"])

    descendants = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        for child_id in by_parent.get(current, []):
            if child_id not in descendants:
                descendants.add(child_id)
                stack.append(child_id)

    return descendants


@public_bp.route("/")
def home():
    return render_template("home.html")


@public_bp.route("/analytics/page_visit", methods=["POST"])
def analytics_page_visit():
    payload = request.get_json(silent=True) or {}
    path = payload.get("path") or request.path
    record_page_visit(path)
    return ("", 204)


@public_bp.route("/analytics/video_view", methods=["POST"])
def analytics_video_view():
    payload = request.get_json(silent=True) or {}
    video_id = payload.get("video_id")
    if not video_id:
        return jsonify({"error": "video_id required"}), 400

    record_video_view(video_id)
    return ("", 204)


@public_bp.route("/analytics/video_watch", methods=["POST"])
def analytics_video_watch():
    payload = request.get_json(silent=True) or {}
    video_id = payload.get("video_id")
    current_time = payload.get("current_time")
    delta_seconds = payload.get("delta_seconds")

    if not video_id:
        return jsonify({"error": "video_id required"}), 400

    record_video_watch(video_id, current_time, delta_seconds)
    return ("", 204)


@public_bp.route("/video/<video_id>")
def video_page(video_id):
    conn = get_db()
    video = conn.execute(
        "SELECT * FROM videos WHERE id = ?", (video_id,)
    ).fetchone()
    conn.close()

    if not video:
        abort(404)

    if video["visibility"] == "private" and not session.get("admin_logged_in"):
        abort(403)

    breadcrumbs = [
        {"name": "Home", "url": "/"},
        {"name": (video["display_name"] or video["filename"]), "url": None},
    ]

    return render_template("video_page.html", video=video, breadcrumbs=breadcrumbs)


@public_bp.route("/<path:collection_path>/video/<video_id>")
def collection_video(collection_path, video_id):
    return redirect(f"/{collection_path}?v={video_id}")


@public_bp.route("/<path:collection_path>")
def collection_page(collection_path):
    slugs = collection_path.strip("/").split("/")
    parent_id = None
    conn = get_db()
    collection = None
    path_parts = []
    breadcrumbs = [{"name": "Home", "url": "/"}]

    for slug in slugs:
        collection = conn.execute(
            "SELECT * FROM collections WHERE slug = ? AND parent_id IS ?",
            (slug, parent_id),
        ).fetchone()
        if not collection:
            conn.close()
            abort(404)
        parent_id = collection["id"]
        path_parts.append(collection["slug"])
        breadcrumbs.append(
            {
                "name": collection["name"],
                "url": "/" + "/".join(path_parts),
            }
        )

    if collection is None:
        conn.close()
        abort(404)

    if collection["visibility"] == "private" and not session.get("admin_logged_in"):
        conn.close()
        abort(403)

    if session.get("admin_logged_in"):
        sub_collections = conn.execute(
            "SELECT * FROM collections WHERE parent_id = ?", (collection["id"],)
        ).fetchall()
        videos = conn.execute(
            "SELECT * FROM videos WHERE collection_id = ? ORDER BY sort_order ASC, filename COLLATE NOCASE ASC",
            (collection["id"],),
        ).fetchall()
        descendants = get_descendant_ids(conn, collection["id"])
        parent_options = [
            option for option in get_collection_parent_options(conn)
            if option["id"] != collection["id"] and option["id"] not in descendants
        ]
    else:
        sub_collections = conn.execute(
            "SELECT * FROM collections WHERE parent_id = ? AND visibility = 'public'", (collection["id"],)
        ).fetchall()
        videos = conn.execute(
            "SELECT * FROM videos WHERE collection_id = ? AND visibility = 'public' ORDER BY sort_order ASC, filename COLLATE NOCASE ASC",
            (collection["id"],),
        ).fetchall()
        parent_options = []

    selected_video_id = request.args.get("v")
    selected_video = None
    if selected_video_id:
        selected_video = next((video for video in videos if video["id"] == selected_video_id), None)
    if selected_video is None and videos:
        selected_video = videos[0]

    if breadcrumbs:
        breadcrumbs[-1]["url"] = None

    conn.close()

    return render_template(
        "collection_page.html",
        collection=collection,
        sub_collections=sub_collections,
        videos=videos,
        selected_video=selected_video,
        breadcrumbs=breadcrumbs,
        parent_options=parent_options,
    )


@public_bp.route("/hls/<video_id>/<path:filename>")
def serve_hls(video_id, filename):
    directory = os.path.join(HLS_FOLDER, video_id)
    if not os.path.exists(os.path.join(directory, filename)):
        abort(404)
    return send_from_directory(directory, filename)
