from datetime import datetime, timezone

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .. import audit, login_guard, storage

bp = Blueprint("auth", __name__)


def _client_ip():
    # No ProxyFix / trusted-proxy list yet — mirrors audit._client_ip(). Behind a reverse
    # proxy that doesn't set X-Forwarded-For, every request looks like it comes from the
    # proxy, which would ban it for everyone; set that header at the proxy if so.
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("explorer.accounts"))
    if request.method == "POST":
        ip = _client_ip()
        banned_until = login_guard.is_banned(ip)
        if banned_until:
            remaining_min = max(1, int((banned_until - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
            audit.log("auth", "login_blocked", f"Connexion bloquée (IP bannie) : {ip}",
                      actor="-", status="fail")
            flash(f"Trop de tentatives échouées depuis cette adresse. Réessaie dans ~{remaining_min} min.", "error")
            return render_template("login.html"), 429

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = storage.verify_login(username, password)
        if user is None:
            new_ban = login_guard.record_failure(ip)
            audit.log(
                "auth", "login_failed",
                f"Échec de connexion : « {username or '(vide)'} »",
                actor=username or "-", status="fail",
            )
            if new_ban:
                cfg = storage.get_login_protection()
                audit.log("auth", "login_lockout",
                          f"IP {ip} bannie jusqu'à {new_ban.strftime('%H:%M:%S')} UTC "
                          f"({cfg['max_attempts']} échecs en moins de "
                          f"{cfg['window_minutes']} min)",
                          actor="-", status="fail")
                flash("Trop de tentatives échouées depuis cette adresse — accès temporairement bloqué.", "error")
            else:
                flash("Identifiants invalides", "error")
        else:
            login_guard.record_success(ip)
            session.clear()
            session["user_id"] = user["id"]
            audit.log("auth", "login", "Connexion réussie", actor=user["username"])
            return redirect(url_for("explorer.accounts"))
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user:
        audit.log("auth", "logout", "Déconnexion", actor=g.user["username"])
    session.clear()
    return redirect(url_for("auth.login"))
