from functools import wraps

from flask import redirect, session, url_for


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated
