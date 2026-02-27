import os

from flask import Flask, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from db import get_db, init_db
from hls_utils import convert_to_hls, inspect_hls_state, probe_duration_seconds
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.public import public_bp
from settings import (
    MAX_CONTENT_LENGTH,
    PERMANENT_SESSION_LIFETIME,
    SECRET_KEY,
    SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    STARTUP_HLS_RETRY_ENABLED,
    STARTUP_HLS_RETRY_LIMIT,
    TRUST_PROXY,
    ensure_storage_dirs,
    validate_runtime_settings,
)
from settings import UPLOAD_FOLDER


def run_startup_backfill():
    conn = get_db()
    videos = conn.execute(
        "SELECT id, filename, duration_seconds, hls_progress_pct FROM videos"
    ).fetchall()
    retries_triggered = 0

    for video in videos:
        video_id = video["id"]
        duration_seconds = int(video["duration_seconds"] or 0)
        hls_progress_pct = int(video["hls_progress_pct"] or 0)
        media_path = os.path.join(UPLOAD_FOLDER, f"{video_id}_{video['filename']}")

        if duration_seconds <= 0 and os.path.exists(media_path):
            duration_seconds = probe_duration_seconds(media_path)

        hls_state = inspect_hls_state(video_id)

        should_retry = (
            STARTUP_HLS_RETRY_ENABLED
            and retries_triggered < STARTUP_HLS_RETRY_LIMIT
            and os.path.exists(media_path)
            and hls_state["status"] in {"missing", "processing", "pending"}
        )
        if should_retry:
            convert_to_hls(video_id, media_path, duration_seconds=duration_seconds)
            retries_triggered += 1
            hls_state["status"] = "processing"
            hls_progress_pct = 0

        if hls_state["status"] == "complete":
            hls_progress_pct = 100
            hls_step = "done"
            hls_error = None
        elif hls_state["status"] == "processing":
            hls_progress_pct = min(hls_progress_pct, 99)
            hls_step = "encoding"
            hls_error = None
        elif hls_state["status"] == "missing":
            hls_progress_pct = 0
            hls_step = "missing"
            hls_error = None
        elif should_retry:
            hls_progress_pct = 0
            hls_step = "retrying"
            hls_error = None
        else:
            hls_step = "pending"
            hls_error = None

        conn.execute(
            """
            UPDATE videos
            SET duration_seconds = ?,
                hls_status = ?,
                hls_progress_pct = ?,
                hls_step = ?,
                hls_error = ?,
                hls_segments_generated = ?,
                hls_segments_expected = ?
            WHERE id = ?
            """,
            (
                duration_seconds,
                hls_state["status"],
                hls_progress_pct,
                hls_step,
                hls_error,
                hls_state["segments_generated"],
                hls_state["segments_expected"],
                video_id,
            ),
        )

    conn.commit()
    conn.close()


def create_app():
    validate_runtime_settings()
    ensure_storage_dirs()
    init_db()
    run_startup_backfill()

    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
    app.config["SESSION_COOKIE_HTTPONLY"] = SESSION_COOKIE_HTTPONLY
    app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE
    app.config["PERMANENT_SESSION_LIFETIME"] = PERMANENT_SESSION_LIFETIME

    if TRUST_PROXY:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(public_bp)

    @app.template_filter("duration_label")
    def duration_label(value):
        try:
            total = int(value or 0)
        except (TypeError, ValueError):
            total = 0

        if total <= 0:
            return "--:--"

        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @app.errorhandler(403)
    def forbidden(error):
        message = getattr(error, "description", None) or "You do not have permission to view this page."
        return render_template("403.html", message=message), 403

    @app.route("/healthz")
    def healthz():
        try:
            conn = get_db()
            conn.execute("SELECT 1")
            conn.close()
            return jsonify({"status": "ok"}), 200
        except Exception:
            return jsonify({"status": "error"}), 503

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))