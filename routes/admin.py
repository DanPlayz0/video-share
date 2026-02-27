import os
import sqlite3
import uuid

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from db import get_collection_parent_options, get_db
from decorators import admin_required
from hls_utils import (
    convert_to_hls,
    get_runtime_hls_progress,
    inspect_hls_state,
    probe_duration_seconds,
)
from settings import UPLOAD_FOLDER

admin_bp = Blueprint("admin", __name__)
ALLOWED_VISIBILITY = {"public", "unlisted", "private"}


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


@admin_bp.route("/admin")
@admin_required
def admin_panel():
    conn = get_db()
    collections = get_collection_parent_options(conn)
    conn.close()
    return render_template("admin_panel.html", collections=collections)


@admin_bp.route("/create_collection", methods=["GET", "POST"])
@admin_required
def create_collection():
    conn = get_db()
    parent_options = get_collection_parent_options(conn)
    conn.close()

    if request.method == "POST":
        name = request.form["name"]
        slug = secure_filename(request.form["slug"])
        parent_id = request.form.get("parent_id") or None
        visibility = request.form.get("visibility") or "public"
        if visibility not in ALLOWED_VISIBILITY:
            visibility = "public"
        collection_id = str(uuid.uuid4())

        conn = get_db()
        conn.execute(
            "INSERT INTO collections (id, name, slug, parent_id, visibility) VALUES (?, ?, ?, ?, ?)",
            (collection_id, name, slug, parent_id, visibility),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("admin.admin_panel"))

    return render_template("create_collection.html", parent_options=parent_options)


@admin_bp.route("/admin/collection/<collection_id>/settings", methods=["POST"])
@admin_required
def update_collection_settings(collection_id):
    return_path = request.form.get("return_path") or "/admin"
    if not return_path.startswith("/"):
        return_path = "/admin"

    conn = get_db()
    current = conn.execute(
        "SELECT id, name, slug, parent_id, visibility FROM collections WHERE id = ?",
        (collection_id,),
    ).fetchone()
    if not current:
        conn.close()
        abort(404)

    visibility = request.form.get("visibility") or current["visibility"] or "public"
    if visibility not in ALLOWED_VISIBILITY:
        visibility = "public"

    updated_name = (request.form.get("name") or "").strip() or current["name"]

    raw_slug = (request.form.get("slug") or "").strip()
    if raw_slug:
        updated_slug = secure_filename(raw_slug) or current["slug"]
    else:
        updated_slug = current["slug"]

    parent_id = request.form.get("parent_id") or None
    if parent_id == collection_id:
        conn.close()
        abort(400)

    if parent_id:
        parent = conn.execute(
            "SELECT id FROM collections WHERE id = ?",
            (parent_id,),
        ).fetchone()
        if not parent:
            conn.close()
            abort(400)

        descendants = get_descendant_ids(conn, collection_id)
        if parent_id in descendants:
            conn.close()
            abort(400)

    try:
        conn.execute(
            "UPDATE collections SET name = ?, slug = ?, parent_id = ?, visibility = ? WHERE id = ?",
            (updated_name, updated_slug, parent_id, visibility, collection_id),
        )
    except sqlite3.IntegrityError:
        conn.close()
        abort(400)

    conn.commit()
    conn.close()

    return redirect(return_path)


@admin_bp.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    conn = get_db()
    collection_options = get_collection_parent_options(conn)
    conn.close()

    if request.method == "POST":
        file = request.files["file"]
        display_name = (request.form.get("display_name") or "").strip()
        description = (request.form.get("description") or "").strip()
        sort_order_input = (request.form.get("sort_order") or "").strip()
        visibility = request.form.get("visibility", "public")
        collection_id = request.form.get("collection_id")
        return_path = request.form.get("return_path") or ""

        if not file or not collection_id:
            abort(400)

        original_filename = file.filename or ""
        if not original_filename:
            abort(400)

        video_id = str(uuid.uuid4())
        filename = secure_filename(original_filename)
        final_display_name = display_name or filename
        save_path = os.path.join(UPLOAD_FOLDER, video_id + "_" + filename)
        file.save(save_path)
        duration_seconds = probe_duration_seconds(save_path)
        hls_state = inspect_hls_state(video_id)

        conn = get_db()
        max_order_row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS max_order FROM videos WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()
        next_sort_order = int(max_order_row["max_order"]) + 1

        try:
            sort_order = int(sort_order_input) if sort_order_input else next_sort_order
        except ValueError:
            sort_order = next_sort_order

        conn.execute(
            "INSERT INTO videos (id, filename, display_name, description, duration_seconds, hls_status, hls_progress_pct, hls_step, hls_error, hls_segments_generated, hls_segments_expected, sort_order, visibility, collection_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video_id,
                filename,
                final_display_name,
                description,
                duration_seconds,
                hls_state["status"],
                0,
                "pending",
                None,
                hls_state["segments_generated"],
                hls_state["segments_expected"],
                sort_order,
                visibility,
                collection_id,
            ),
        )
        conn.commit()
        conn.close()

        convert_to_hls(video_id, save_path, duration_seconds=duration_seconds)

        if return_path.startswith("/"):
            return redirect(return_path)

        return redirect(url_for("admin.admin_panel"))

    return render_template("upload.html", collection_options=collection_options)


@admin_bp.route("/admin/playlist/<collection_id>", methods=["POST"])
@admin_required
def update_playlist(collection_id):
    return_path = request.form.get("return_path") or "/admin"
    if not return_path.startswith("/"):
        return_path = "/admin"

    conn = get_db()
    videos = conn.execute(
        "SELECT id, filename, display_name, description, sort_order, hls_status, hls_segments_generated, hls_segments_expected FROM videos WHERE collection_id = ?",
        (collection_id,),
    ).fetchall()

    for video in videos:
        video_id = video["id"]
        name_key = f"title_{video_id}"
        description_key = f"description_{video_id}"
        order_key = f"order_{video_id}"

        updated_name = (request.form.get(name_key) or "").strip()
        if not updated_name:
            updated_name = video["filename"]

        updated_description = (request.form.get(description_key) or "").strip()

        order_input = (request.form.get(order_key) or "").strip()
        try:
            updated_order = int(order_input)
        except ValueError:
            updated_order = video["sort_order"] or 0

        conn.execute(
            "UPDATE videos SET display_name = ?, description = ?, sort_order = ? WHERE id = ? AND collection_id = ?",
            (updated_name, updated_description, updated_order, video_id, collection_id),
        )

    conn.commit()
    conn.close()

    return redirect(return_path)


@admin_bp.route("/admin/hls_progress/<collection_id>")
@admin_required
def hls_progress(collection_id):
    conn = get_db()
    videos = conn.execute(
        """
        SELECT id, hls_status, hls_progress_pct, hls_step, hls_error,
               hls_segments_generated, hls_segments_expected
        FROM videos
        WHERE collection_id = ?
        """,
        (collection_id,),
    ).fetchall()
    conn.close()

    result = []
    for row in videos:
        runtime = get_runtime_hls_progress(row["id"])
        if runtime:
            status = runtime.get("status") or row["hls_status"]
            progress_pct = int(runtime.get("progress_pct") or 0)
            step = runtime.get("step") or row["hls_step"] or ""
            error = runtime.get("error") or row["hls_error"] or ""
            segments_generated = int(runtime.get("segments_generated") or row["hls_segments_generated"] or 0)
            segments_expected = int(runtime.get("segments_expected") or row["hls_segments_expected"] or 0)
        else:
            status = row["hls_status"]
            progress_pct = int(row["hls_progress_pct"] or 0)
            step = row["hls_step"] or ""
            error = row["hls_error"] or ""
            segments_generated = int(row["hls_segments_generated"] or 0)
            segments_expected = int(row["hls_segments_expected"] or 0)

        result.append(
            {
                "id": row["id"],
                "status": status,
                "progress_pct": progress_pct,
                "step": step,
                "error": error,
                "segments_generated": segments_generated,
                "segments_expected": segments_expected,
            }
        )

    return jsonify({"videos": result})
