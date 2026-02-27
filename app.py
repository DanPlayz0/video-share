import os

from flask import Flask, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from db import get_db, init_db
from hls_utils import inspect_hls_state, probe_duration_seconds
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
    TRUST_PROXY,
    ensure_storage_dirs,
    validate_runtime_settings,
)
from settings import UPLOAD_FOLDER


def run_startup_backfill():
    conn = get_db()
    videos = conn.execute(
        "SELECT id, filename, duration_seconds FROM videos"
    ).fetchall()

    for video in videos:
        video_id = video["id"]
        duration_seconds = int(video["duration_seconds"] or 0)
        media_path = os.path.join(UPLOAD_FOLDER, f"{video_id}_{video['filename']}")

        if duration_seconds <= 0 and os.path.exists(media_path):
            duration_seconds = probe_duration_seconds(media_path)

        hls_state = inspect_hls_state(video_id)

        conn.execute(
            """
            UPDATE videos
            SET duration_seconds = ?,
                hls_status = ?,
                hls_segments_generated = ?,
                hls_segments_expected = ?
            WHERE id = ?
            """,
            (
                duration_seconds,
                hls_state["status"],
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