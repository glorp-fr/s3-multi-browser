from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from .. import audit, backup, storage, sync, version
from ..auth import PERMISSIONS, admin_required
from .sync_routes import job_view as _sync_job_view

bp = Blueprint("admin", __name__, url_prefix="/admin")


# --- Version / mises à jour ----------------------------------------------

@bp.route("/version")
@admin_required
def version_page():
    state = version.local_state()
    return render_template(
        "admin_version.html",
        state=state,
        check=version.cached_check(),
        repo=version.REPO,
        mode="git" if state["is_git"] else "image",
        updater_ready=bool(version.UPDATE_TOKEN),
    )


@bp.route("/version/check", methods=["POST"])
@admin_required
def version_check():
    result = version.check_update()
    if result.get("error"):
        audit.log("admin", "version_check", f"Vérification MAJ échouée : {result['error']}", status="fail")
        flash(result["error"], "error")
    elif result.get("up_to_date"):
        audit.log("admin", "version_check", "Vérification MAJ : à jour")
        flash("L'application est à jour.", "success")
    else:
        if result.get("mode") == "image":
            msg = f"Mise à jour disponible : version {result.get('latest_version') or '?'}."
        else:
            msg = f"Mise à jour disponible : {result['behind_by']} commit(s) de retard."
        audit.log("admin", "version_check", f"Vérification MAJ : {msg}")
        flash(msg, "success")
    return redirect(url_for("admin.version_page"))


@bp.route("/version/update", methods=["POST"])
@admin_required
def version_update():
    result = version.apply_update()
    if not result["ok"]:
        audit.log("admin", "version_update", f"Mise à jour refusée : {result['message']}", status="fail")
        flash(result["message"], "error")
    elif not result["changed"]:
        audit.log("admin", "version_update", "Mise à jour : déjà à jour")
        flash(result["message"], "success")
    else:
        old, new = result.get("old"), result.get("new")
        if old and new:
            detail = f"Mise à jour appliquée {old[:7]} → {new[:7]}"
            target = new[:7]
        else:
            detail = result.get("message", "Mise à jour lancée")
            target = None
        audit.log("admin", "version_update",
                  detail + (" (rechargement en cours)" if result.get("reload") else ""),
                  target=target)
        flash(result["message"], "success")
    return redirect(url_for("admin.version_page"))


# --- Sauvegarde de configuration -------------------------------------------

@bp.route("/backup", methods=["GET", "POST"])
@admin_required
def backup_page():
    if request.method == "POST":
        f = request.form
        try:
            storage.update_backup_config(
                enabled=f.get("enabled"),
                destination=f.get("destination", "s3"),
                frequency=f.get("frequency", "daily"),
                hour=f.get("hour", 3), minute=f.get("minute", 0),
                weekday=f.get("weekday", 0), retention=f.get("retention", 7),
                s3={k: f.get(f"s3_{k}", "") for k in
                    ("endpoint", "region", "access_key", "bucket", "prefix", "secret_key")},
                smb={k: f.get(f"smb_{k}", "") for k in
                     ("server", "share", "path", "domain", "username", "password")},
            )
            backup.reschedule()
            audit.log("admin", "backup_config", "Configuration de sauvegarde mise à jour")
            flash("Configuration de sauvegarde enregistrée", "success")
            return redirect(url_for("admin.backup_page"))
        except ValueError as exc:
            flash(str(exc), "error")

    backups, backups_error = [], None
    try:
        backups = backup.list_backups()
    except Exception as exc:  # noqa: BLE001 - misconfigured/unreachable destination
        backups_error = str(exc)
    return render_template("admin_backup.html", cfg=storage.get_backup_config(),
                           next_run=backup.next_run_display(),
                           backups=backups, backups_error=backups_error)


@bp.route("/backup/run", methods=["POST"])
@admin_required
def backup_run():
    result = backup.run_backup(actor=g.user["username"])
    flash(result["message"], "success" if result["ok"] else "error")
    return redirect(url_for("admin.backup_page"))


@bp.route("/backup/restore", methods=["POST"])
@admin_required
def backup_restore():
    name = request.form.get("name", "")
    if not name:
        flash("Choisir une sauvegarde à restaurer", "error")
        return redirect(url_for("admin.backup_page"))
    result = backup.restore_backup(name, actor=g.user["username"])
    flash(result["message"], "success" if result["ok"] else "error")
    return redirect(url_for("admin.backup_page"))


# --- Synchronisation (vue globale) ------------------------------------------
# Chaque utilisateur gère ses propres jobs sur /sync ; cette page admin ne fait que
# superviser : désactiver un job qui déraille, ou le reprendre à son nom (un admin a
# toujours tous les droits, donc la reprise lève systématiquement un blocage de droits).

@bp.route("/sync")
@admin_required
def sync_jobs():
    jobs = [_sync_job_view(j) for j in storage.get_sync_jobs()]
    return render_template("admin_sync.html", jobs=jobs)


@bp.route("/sync/<job_id>/toggle", methods=["POST"])
@admin_required
def sync_job_toggle(job_id):
    job = storage.get_sync_job_by_id(job_id)
    if not job:
        flash("Job introuvable", "error")
        return redirect(url_for("admin.sync_jobs"))
    new_enabled = not job["enabled"]
    storage.set_sync_job_enabled(job_id, new_enabled, state="idle" if new_enabled else job["status"]["state"])
    sync.reschedule(job_id) if new_enabled else sync.cancel(job_id)
    audit.log("admin", "sync_job_toggle",
              f"Job « {job['name']} » {'réactivé' if new_enabled else 'désactivé'} par un admin",
              target=job["name"])
    flash(f"Job {'réactivé' if new_enabled else 'désactivé'}", "success")
    return redirect(url_for("admin.sync_jobs"))


@bp.route("/sync/<job_id>/reassign", methods=["POST"])
@admin_required
def sync_job_reassign(job_id):
    job = storage.get_sync_job_by_id(job_id)
    if not job:
        flash("Job introuvable", "error")
        return redirect(url_for("admin.sync_jobs"))
    try:
        storage.reassign_sync_job(job_id, g.user["id"])
        sync.reschedule(job_id)
        audit.log("admin", "sync_job_reassign",
                  f"Job « {job['name']} » repris par l'admin « {g.user['username']} »",
                  target=job["name"])
        flash("Job repris à votre nom — modifiez-le si besoin pour l'ajuster.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.sync_jobs"))


# --- Logs ------------------------------------------------------------------

@bp.route("/logs")
@admin_required
def logs():
    recent, last_seq = audit.tail(limit=300)
    history = audit.connection_history(q=request.args.get("q", "").strip() or None, limit=200)
    return render_template(
        "admin_logs.html",
        recent=recent, last_seq=last_seq, history=history, categories=audit.CATEGORIES,
    )


@bp.route("/logs/tail")
@admin_required
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
@admin_required
def providers():
    return render_template("admin_providers.html", providers=storage.get_providers(), accounts=storage.get_accounts())


@bp.route("/providers/new", methods=["GET", "POST"])
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
def accounts():
    providers_by_id = {p["id"]: p for p in storage.get_providers()}
    return render_template("admin_accounts.html", accounts=storage.get_accounts(), providers_by_id=providers_by_id)


@bp.route("/accounts/new", methods=["GET", "POST"])
@admin_required
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
@admin_required
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
@admin_required
def account_delete(account_id):
    account = storage.get_account_by_id(account_id)
    storage.delete_account(account_id)
    audit.log("admin", "account_delete",
              f"Compte S3 « {account['name'] if account else account_id} » supprimé",
              target=account["name"] if account else account_id)
    flash("Compte supprimé", "success")
    return redirect(url_for("admin.accounts"))


# --- Groups ----------------------------------------------------------------

def _group_form():
    """(name, permissions, all_accounts, account_ids) from the submitted group form."""
    all_accounts = bool(request.form.get("all_accounts"))
    return (
        request.form.get("name", "").strip(),
        [p for p in request.form.getlist("permissions") if p in PERMISSIONS],
        all_accounts,
        [] if all_accounts else request.form.getlist("account_ids"),
    )


@bp.route("/groups")
@admin_required
def groups():
    accounts_by_id = {a["id"]: a for a in storage.get_accounts()}
    users = storage.get_users()
    member_counts = {}
    for grp in storage.get_groups():
        member_counts[grp["id"]] = sum(1 for u in users if grp["id"] in (u.get("group_ids") or []))
    return render_template(
        "admin_groups.html", groups=storage.get_groups(),
        accounts_by_id=accounts_by_id, member_counts=member_counts, permissions=PERMISSIONS,
    )


@bp.route("/groups/new", methods=["GET", "POST"])
@admin_required
def group_new():
    if request.method == "POST":
        name, perms, all_accounts, account_ids = _group_form()
        try:
            storage.create_group(name, perms, all_accounts, account_ids)
            audit.log("admin", "group_create",
                      f"Groupe « {name} » créé (droits : {', '.join(perms) or 'aucun'})",
                      target=name)
            flash("Groupe créé", "success")
            return redirect(url_for("admin.groups"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_group_form.html", group=None,
                           permissions=PERMISSIONS, accounts=storage.get_accounts())


@bp.route("/groups/<group_id>/edit", methods=["GET", "POST"])
@admin_required
def group_edit(group_id):
    group = storage.get_group_by_id(group_id)
    if not group:
        flash("Groupe introuvable", "error")
        return redirect(url_for("admin.groups"))
    if request.method == "POST":
        name, perms, all_accounts, account_ids = _group_form()
        try:
            storage.update_group(group_id, name, perms, all_accounts, account_ids)
            audit.log("admin", "group_update",
                      f"Groupe « {name} » modifié (droits : {', '.join(perms) or 'aucun'})",
                      target=name)
            flash("Groupe mis à jour", "success")
            return redirect(url_for("admin.groups"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_group_form.html", group=group,
                           permissions=PERMISSIONS, accounts=storage.get_accounts())


@bp.route("/groups/<group_id>/delete", methods=["POST"])
@admin_required
def group_delete(group_id):
    group = storage.get_group_by_id(group_id)
    try:
        storage.delete_group(group_id)
        audit.log("admin", "group_delete",
                  f"Groupe « {group['name'] if group else group_id} » supprimé",
                  target=group["name"] if group else group_id)
        flash("Groupe supprimé", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.groups"))


# --- Users -------------------------------------------------------------------

@bp.route("/users")
@admin_required
def users():
    groups_by_id = {g["id"]: g for g in storage.get_groups()}
    return render_template("admin_users.html", users=storage.get_users(), groups_by_id=groups_by_id)


def _user_form():
    """(is_admin, group_ids) from the submitted user form."""
    is_admin = bool(request.form.get("is_admin"))
    return is_admin, ([] if is_admin else request.form.getlist("group_ids"))


def _access_label(is_admin, group_ids):
    if is_admin:
        return "administrateur"
    return f"{len(group_ids)} groupe(s)"


@bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_new():
    if request.method == "POST":
        username = request.form["username"].strip()
        is_admin, group_ids = _user_form()
        try:
            storage.create_user(
                username=username,
                password=request.form["password"],
                is_admin=is_admin,
                group_ids=group_ids,
            )
            audit.log("admin", "user_create",
                      f"Utilisateur « {username} » créé ({_access_label(is_admin, group_ids)})",
                      target=username)
            flash("Utilisateur créé", "success")
            return redirect(url_for("admin.users"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_user_form.html", user=None, groups=storage.get_groups())


@bp.route("/users/<user_id>/edit", methods=["GET", "POST"])
@admin_required
def user_edit(user_id):
    user = storage.get_user_by_id(user_id)
    if not user:
        flash("Utilisateur introuvable", "error")
        return redirect(url_for("admin.users"))
    if request.method == "POST":
        username = request.form["username"].strip()
        is_admin, group_ids = _user_form()
        pw_changed = bool(request.form.get("password"))
        try:
            storage.update_user(
                user_id,
                username=username,
                is_admin=is_admin,
                group_ids=group_ids,
                password=request.form.get("password") or None,
            )
            audit.log("admin", "user_update",
                      f"Utilisateur « {username} » modifié ({_access_label(is_admin, group_ids)})"
                      + (" — mot de passe changé" if pw_changed else ""),
                      target=username)
            flash("Utilisateur mis à jour", "success")
            return redirect(url_for("admin.users"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_user_form.html", user=user, groups=storage.get_groups())


@bp.route("/users/<user_id>/delete", methods=["POST"])
@admin_required
def user_delete(user_id):
    user = storage.get_user_by_id(user_id)
    try:
        storage.delete_user(user_id)
        audit.log("admin", "user_delete",
                  f"Utilisateur « {user['username'] if user else user_id} » supprimé",
                  target=user["username"] if user else user_id)
        flash("Utilisateur supprimé", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.users"))
