import os
import uuid

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from db import get_collection_parent_options, get_db
from decorators import admin_required
from hls_utils import convert_to_hls, inspect_hls_state, probe_duration_seconds
from settings import UPLOAD_FOLDER

admin_bp = Blueprint("admin", __name__)
ALLOWED_VISIBILITY = {"public", "unlisted", "private"}


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

    visibility = request.form.get("visibility") or "public"
    if visibility not in ALLOWED_VISIBILITY:
        visibility = "public"

    conn = get_db()
    conn.execute(
        "UPDATE collections SET visibility = ? WHERE id = ?",
        (visibility, collection_id),
    )
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
            "INSERT INTO videos (id, filename, display_name, duration_seconds, hls_status, hls_segments_generated, hls_segments_expected, sort_order, visibility, collection_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video_id,
                filename,
                final_display_name,
                duration_seconds,
                hls_state["status"],
                hls_state["segments_generated"],
                hls_state["segments_expected"],
                sort_order,
                visibility,
                collection_id,
            ),
        )
        conn.commit()
        conn.close()

        convert_to_hls(video_id, save_path)

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
        "SELECT id, filename, display_name, sort_order, hls_status, hls_segments_generated, hls_segments_expected FROM videos WHERE collection_id = ?",
        (collection_id,),
    ).fetchall()

    for video in videos:
        video_id = video["id"]
        name_key = f"title_{video_id}"
        order_key = f"order_{video_id}"

        updated_name = (request.form.get(name_key) or "").strip()
        if not updated_name:
            updated_name = video["filename"]

        order_input = (request.form.get(order_key) or "").strip()
        try:
            updated_order = int(order_input)
        except ValueError:
            updated_order = video["sort_order"] or 0

        conn.execute(
            "UPDATE videos SET display_name = ?, sort_order = ? WHERE id = ? AND collection_id = ?",
            (updated_name, updated_order, video_id, collection_id),
        )

    conn.commit()
    conn.close()

    return redirect(return_path)
