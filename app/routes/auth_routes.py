from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .. import storage

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("explorer.accounts"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = storage.verify_login(username, password)
        if user is None:
            flash("Identifiants invalides", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("explorer.accounts"))
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
