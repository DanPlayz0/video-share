import os
from datetime import timedelta

from werkzeug.security import generate_password_hash

STORAGE_ROOT = os.getenv("STORAGE_ROOT", "storage")
UPLOAD_FOLDER = os.path.join(STORAGE_ROOT, "media")
HLS_FOLDER = os.path.join(STORAGE_ROOT, "hls")
DATABASE = os.getenv("DATABASE_PATH", os.path.join(STORAGE_ROOT, "database.db"))
SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_THIS_SECRET")
APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")

_plain_admin_password = os.getenv("ADMIN_PASSWORD", "change_this_password")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")
if not ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = generate_password_hash(_plain_admin_password)

SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true" if IS_PRODUCTION else "false").lower() == "true"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
PERMANENT_SESSION_LIFETIME = timedelta(days=int(os.getenv("SESSION_LIFETIME_DAYS", "14")))

TRUST_PROXY = os.getenv("TRUST_PROXY", "true" if IS_PRODUCTION else "false").lower() == "true"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2048"))
MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
STARTUP_HLS_RETRY_ENABLED = os.getenv("STARTUP_HLS_RETRY_ENABLED", "true").lower() == "true"
STARTUP_HLS_RETRY_LIMIT = int(os.getenv("STARTUP_HLS_RETRY_LIMIT", "50"))


def validate_runtime_settings():
    if not IS_PRODUCTION:
        return

    insecure_secret = (not SECRET_KEY) or SECRET_KEY == "CHANGE_THIS_SECRET"
    insecure_admin = (
        not os.getenv("ADMIN_PASSWORD_HASH")
        and _plain_admin_password == "change_this_password"
    )

    if insecure_secret:
        raise RuntimeError("SECRET_KEY must be set to a strong value in production")
    if insecure_admin:
        raise RuntimeError("Set ADMIN_PASSWORD or ADMIN_PASSWORD_HASH to a non-default value in production")


def ensure_storage_dirs():
    os.makedirs(STORAGE_ROOT, exist_ok=True)
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(HLS_FOLDER, exist_ok=True)
