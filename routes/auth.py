from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from settings import ADMIN_PASSWORD_HASH, ADMIN_USERNAME

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["admin_logged_in"] = True
            return redirect(url_for("admin.admin_panel"))
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("public.home"))
