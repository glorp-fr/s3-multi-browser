from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from .. import audit, storage, version
from ..auth import ROLES, role_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


# --- Version / mises à jour ----------------------------------------------

@bp.route("/version")
@role_required("admin")
def version_page():
    return render_template(
        "admin_version.html",
        state=version.local_state(),
        check=version.cached_check(),
        repo=version.REPO,
    )


@bp.route("/version/check", methods=["POST"])
@role_required("admin")
def version_check():
    result = version.check_update()
    if result.get("error"):
        audit.log("admin", "version_check", f"Vérification MAJ échouée : {result['error']}", status="fail")
        flash(result["error"], "error")
    elif result.get("up_to_date"):
        audit.log("admin", "version_check", "Vérification MAJ : à jour")
        flash("L'application est à jour.", "success")
    else:
        audit.log("admin", "version_check",
                  f"Vérification MAJ : {result['behind_by']} commit(s) de retard")
        flash(f"Mise à jour disponible : {result['behind_by']} commit(s) de retard.", "success")
    return redirect(url_for("admin.version_page"))


@bp.route("/version/update", methods=["POST"])
@role_required("admin")
def version_update():
    result = version.apply_update()
    if not result["ok"]:
        audit.log("admin", "version_update", f"Mise à jour refusée : {result['message']}", status="fail")
        flash(result["message"], "error")
    elif not result["changed"]:
        audit.log("admin", "version_update", "Mise à jour : déjà à jour")
        flash(result["message"], "success")
    else:
        audit.log("admin", "version_update",
                  f"Mise à jour appliquée {result['old'][:7]} → {result['new'][:7]}"
                  + (" (rechargement en cours)" if result.get("reload") else ""),
                  target=result["new"][:7])
        flash(result["message"], "success")
    return redirect(url_for("admin.version_page"))


# --- Logs ------------------------------------------------------------------

@bp.route("/logs")
@role_required("admin")
def logs():
    recent, last_seq = audit.tail(limit=300)
    history = audit.connection_history(q=request.args.get("q", "").strip() or None, limit=200)
    return render_template(
        "admin_logs.html",
        recent=recent, last_seq=last_seq, history=history, categories=audit.CATEGORIES,
    )


@bp.route("/logs/tail")
@role_required("admin")
def logs_tail():
    try:
        after = int(request.args.get("after", ""))
    except ValueError:
        after = None
    q = request.args.get("q", "").strip() or None
    cats = [c for c in request.args.get("cat", "").split(",") if c] or None
    records, last_seq = audit.tail(after_seq=after, q=q, categories=cats, limit=500)
    return jsonify(records=records, last_seq=last_seq)


# --- Providers ---------------------------------------------------------------

@bp.route("/providers")
@role_required("admin")
def providers():
    return render_template("admin_providers.html", providers=storage.get_providers(), accounts=storage.get_accounts())


@bp.route("/providers/new", methods=["GET", "POST"])
@role_required("admin")
def provider_new():
    if request.method == "POST":
        name = request.form["name"].strip()
        try:
            storage.create_provider(
                name=name,
                endpoint_template=request.form["endpoint_template"].strip(),
                raw_regions=request.form.get("regions", "").splitlines(),
            )
            audit.log("admin", "provider_create", f"Provider « {name} » créé", target=name)
            flash("Provider créé", "success")
            return redirect(url_for("admin.providers"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_provider_form.html", provider=None)


@bp.route("/providers/<provider_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def provider_edit(provider_id):
    provider = storage.get_provider_by_id(provider_id)
    if not provider:
        flash("Provider introuvable", "error")
        return redirect(url_for("admin.providers"))
    if request.method == "POST":
        name = request.form["name"].strip()
        try:
            storage.update_provider(
                provider_id,
                name=name,
                endpoint_template=request.form["endpoint_template"].strip(),
                raw_regions=request.form.get("regions", "").splitlines(),
            )
            audit.log("admin", "provider_update", f"Provider « {name} » modifié", target=name)
            flash("Provider mis à jour", "success")
            return redirect(url_for("admin.providers"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_provider_form.html", provider=provider)


@bp.route("/providers/<provider_id>/delete", methods=["POST"])
@role_required("admin")
def provider_delete(provider_id):
    provider = storage.get_provider_by_id(provider_id)
    try:
        storage.delete_provider(provider_id)
        audit.log("admin", "provider_delete",
                  f"Provider « {provider['name'] if provider else provider_id} » supprimé",
                  target=provider["name"] if provider else provider_id)
        flash("Provider supprimé", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.providers"))


# --- Accounts ---------------------------------------------------------------

@bp.route("/accounts")
@role_required("admin")
def accounts():
    providers_by_id = {p["id"]: p for p in storage.get_providers()}
    return render_template("admin_accounts.html", accounts=storage.get_accounts(), providers_by_id=providers_by_id)


@bp.route("/accounts/new", methods=["GET", "POST"])
@role_required("admin")
def account_new():
    if request.method == "POST":
        name = request.form["name"].strip()
        try:
            storage.create_account(
                name=name,
                account_number=request.form.get("account_number", "").strip(),
                provider_id=request.form["provider_id"],
                region=request.form["region"].strip(),
                access_key=request.form["access_key"].strip(),
                secret_key=request.form["secret_key"].strip(),
            )
            audit.log("admin", "account_create", f"Compte S3 « {name} » créé", target=name)
            flash("Compte créé", "success")
            return redirect(url_for("admin.accounts"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_account_form.html", account=None, providers=storage.get_providers())


@bp.route("/accounts/<account_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def account_edit(account_id):
    account = storage.get_account_by_id(account_id)
    if not account:
        flash("Compte introuvable", "error")
        return redirect(url_for("admin.accounts"))
    if request.method == "POST":
        name = request.form["name"].strip()
        secret_changed = bool(request.form.get("secret_key", "").strip())
        try:
            storage.update_account(
                account_id,
                name=name,
                account_number=request.form.get("account_number", "").strip(),
                provider_id=request.form["provider_id"],
                region=request.form["region"].strip(),
                access_key=request.form["access_key"].strip(),
                secret_key=request.form.get("secret_key", "").strip() or None,
            )
            audit.log("admin", "account_update",
                      f"Compte S3 « {name} » modifié"
                      + (" (Secret Key changée)" if secret_changed else ""),
                      target=name)
            flash("Compte mis à jour", "success")
            return redirect(url_for("admin.accounts"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_account_form.html", account=account, providers=storage.get_providers())


@bp.route("/accounts/<account_id>/delete", methods=["POST"])
@role_required("admin")
def account_delete(account_id):
    account = storage.get_account_by_id(account_id)
    storage.delete_account(account_id)
    audit.log("admin", "account_delete",
              f"Compte S3 « {account['name'] if account else account_id} » supprimé",
              target=account["name"] if account else account_id)
    flash("Compte supprimé", "success")
    return redirect(url_for("admin.accounts"))


# --- Users -------------------------------------------------------------------

@bp.route("/users")
@role_required("admin")
def users():
    return render_template("admin_users.html", users=storage.get_users(), accounts=storage.get_accounts())


def _account_ids_from_form():
    if request.form.get("all_accounts"):
        return "*"
    return request.form.getlist("account_ids")


@bp.route("/users/new", methods=["GET", "POST"])
@role_required("admin")
def user_new():
    if request.method == "POST":
        username = request.form["username"].strip()
        role = request.form["role"]
        try:
            storage.create_user(
                username=username,
                password=request.form["password"],
                role=role,
                account_ids=_account_ids_from_form(),
            )
            audit.log("admin", "user_create",
                      f"Utilisateur « {username} » créé (rôle {role})", target=username)
            flash("Utilisateur créé", "success")
            return redirect(url_for("admin.users"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_user_form.html", user=None, roles=ROLES, accounts=storage.get_accounts())


@bp.route("/users/<user_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def user_edit(user_id):
    user = storage.get_user_by_id(user_id)
    if not user:
        flash("Utilisateur introuvable", "error")
        return redirect(url_for("admin.users"))
    if request.method == "POST":
        username = request.form["username"].strip()
        role = request.form["role"]
        pw_changed = bool(request.form.get("password"))
        try:
            storage.update_user(
                user_id,
                username=username,
                role=role,
                account_ids=_account_ids_from_form(),
                password=request.form.get("password") or None,
            )
            audit.log("admin", "user_update",
                      f"Utilisateur « {username} » modifié (rôle {role})"
                      + (" — mot de passe changé" if pw_changed else ""),
                      target=username)
            flash("Utilisateur mis à jour", "success")
            return redirect(url_for("admin.users"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_user_form.html", user=user, roles=ROLES, accounts=storage.get_accounts())


@bp.route("/users/<user_id>/delete", methods=["POST"])
@role_required("admin")
def user_delete(user_id):
    user = storage.get_user_by_id(user_id)
    storage.delete_user(user_id)
    audit.log("admin", "user_delete",
              f"Utilisateur « {user['username'] if user else user_id} » supprimé",
              target=user["username"] if user else user_id)
    flash("Utilisateur supprimé", "success")
    return redirect(url_for("admin.users"))
