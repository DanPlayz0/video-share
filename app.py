import os

from flask import Flask, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from db import get_db, init_db
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


def create_app():
    validate_runtime_settings()
    ensure_storage_dirs()
    init_db()

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