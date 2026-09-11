"""Administration-free "Synchronisation" pages: any user with `download` on at least one
account and `upload` on at least one (possibly different) account can create jobs. Each
user only sees/edits their own jobs here — cross-user oversight is `/admin/sync`
(admin_routes.py)."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import audit, storage, sync
from ..auth import accounts_with_permission, can, login_required

bp = Blueprint("sync", __name__, url_prefix="/sync")


def _account_label(account_id):
    account = storage.get_account_by_id(account_id)
    return account["name"] if account else "(compte supprimé)"


def _source_label(source):
    label = f"{_account_label(source['account_id'])}/{source['bucket']}"
    if source["scope"] == "object":
        return label + f" :: {source['value']}"
    if source["scope"] == "prefix":
        return label + f"/{source['value']}"
    if source["scope"] == "selection":
        return label + f"/{source['value']} ({len(source.get('keys') or [])} élément(s))"
    return label + " (bucket entier)"


def _dest_label(dest):
    label = f"{_account_label(dest['account_id'])}/{dest['bucket']}"
    return label + (f"/{dest['prefix']}" if dest["prefix"] else "")


def job_view(job):
    progress = sync.get_progress(job["id"])
    owner = storage.get_user_by_id(job["owner_id"])
    return {
        "id": job["id"],
        "name": job["name"],
        "source_label": _source_label(job["source"]),
        "dest_label": _dest_label(job["dest"]),
        "delete_extraneous": job["delete_extraneous"],
        "enabled": job["enabled"],
        "schedule_enabled": job["schedule"]["enabled"],
        # Jobs born from the explorer's "Copier vers…" bulk action carry a frozen list of
        # keys ("selection" scope) — not something the object/prefix/bucket form can edit.
        "editable": job["source"]["scope"] != "selection",
        "state": "running" if progress else job["status"]["state"],
        "last_run_at": job["status"]["last_run_at"],
        "last_summary": job["status"]["last_summary"],
        "next_run_at": sync.next_run_display(job),
        "progress": progress,
        "owner_username": owner["username"] if owner else "(supprimé)",
        "owner_id": job["owner_id"],
    }


def _own_job_or_404(job_id):
    job = storage.get_sync_job_by_id(job_id)
    if not job or job["owner_id"] != g.user["id"]:
        abort(404)
    return job


def _form_to_job_kwargs():
    f = request.form
    return dict(
        name=f.get("name", ""),
        source={
            "account_id": f.get("source_account_id", ""),
            "bucket": f.get("source_bucket", ""),
            "scope": f.get("source_scope", "prefix"),
            "value": f.get("source_value", ""),
        },
        dest={
            "account_id": f.get("dest_account_id", ""),
            "bucket": f.get("dest_bucket", ""),
            "prefix": f.get("dest_prefix", ""),
        },
        delete_extraneous=bool(f.get("delete_extraneous")),
        schedule={
            "enabled": bool(f.get("schedule_enabled")),
            "frequency": f.get("frequency", "daily"),
            "weekday": f.get("weekday", 0),
            "hour": f.get("hour", 3),
            "minute": f.get("minute", 0),
        },
    )


def _check_form_rights(kwargs):
    """Defense in depth: the form only lists eligible accounts, but a direct POST could
    name any account id — re-check server-side before touching storage."""
    if not can(g.user, kwargs["source"]["account_id"], "download"):
        return "Vous n'avez pas le droit de téléchargement sur le compte source choisi."
    if not can(g.user, kwargs["dest"]["account_id"], "upload"):
        return "Vous n'avez pas le droit de dépôt sur le compte destination choisi."
    if kwargs["delete_extraneous"] and not can(g.user, kwargs["dest"]["account_id"], "delete"):
        return "La suppression des objets absents nécessite le droit de suppression sur le compte destination."
    return None


@bp.route("")
@login_required
def dashboard():
    jobs = [job_view(j) for j in storage.get_sync_jobs_by_owner(g.user["id"])]
    can_create = bool(accounts_with_permission(g.user, "download")) and bool(accounts_with_permission(g.user, "upload"))
    return render_template("sync_jobs.html", jobs=jobs, can_create=can_create)


@bp.route("/status")
@login_required
def status():
    jobs = storage.get_sync_jobs_by_owner(g.user["id"])
    out = []
    for job in jobs:
        progress = sync.get_progress(job["id"])
        out.append({
            "id": job["id"],
            "state": "running" if progress else job["status"]["state"],
            "enabled": job["enabled"],
            "done": progress["done"] if progress else None,
            "total": progress["total"] if progress else None,
            "last_run_at": job["status"]["last_run_at"],
            "last_summary": job["status"]["last_summary"],
            "next_run_at": sync.next_run_display(job),
        })
    return jsonify(jobs=out)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def job_new():
    source_accounts = accounts_with_permission(g.user, "download")
    dest_accounts = accounts_with_permission(g.user, "upload")
    if request.method == "POST":
        kwargs = _form_to_job_kwargs()
        err = _check_form_rights(kwargs)
        if err:
            flash(err, "error")
        else:
            try:
                job = storage.create_sync_job(owner_id=g.user["id"], **kwargs)
                sync.reschedule(job["id"])
                audit.log("s3_write", "sync_job_create", f"Job de synchronisation « {job['name']} » créé",
                          target=job["name"])
                flash("Job de synchronisation créé", "success")
                return redirect(url_for("sync.dashboard"))
            except ValueError as exc:
                flash(str(exc), "error")
    return render_template("sync_job_form.html", job=None,
                           source_accounts=source_accounts, dest_accounts=dest_accounts)


@bp.route("/<job_id>/edit", methods=["GET", "POST"])
@login_required
def job_edit(job_id):
    job = _own_job_or_404(job_id)
    if job["source"]["scope"] == "selection":
        flash("Ce job vient d'une copie manuelle (sélection d'objets figée) : non modifiable, "
              "supprimez-le si besoin.", "error")
        return redirect(url_for("sync.dashboard"))
    source_accounts = accounts_with_permission(g.user, "download")
    dest_accounts = accounts_with_permission(g.user, "upload")
    if request.method == "POST":
        kwargs = _form_to_job_kwargs()
        err = _check_form_rights(kwargs)
        if err:
            flash(err, "error")
        else:
            try:
                job = storage.update_sync_job(job_id, **kwargs)
                sync.reschedule(job_id)
                audit.log("s3_write", "sync_job_update", f"Job de synchronisation « {job['name']} » modifié",
                          target=job["name"])
                flash("Job de synchronisation mis à jour", "success")
                return redirect(url_for("sync.dashboard"))
            except ValueError as exc:
                flash(str(exc), "error")
    return render_template("sync_job_form.html", job=job,
                           source_accounts=source_accounts, dest_accounts=dest_accounts)


@bp.route("/<job_id>/delete", methods=["POST"])
@login_required
def job_delete(job_id):
    job = _own_job_or_404(job_id)
    sync.cancel(job_id)
    storage.delete_sync_job(job_id)
    audit.log("s3_write", "sync_job_delete", f"Job de synchronisation « {job['name']} » supprimé",
              target=job["name"])
    flash("Job supprimé", "success")
    return redirect(url_for("sync.dashboard"))


@bp.route("/<job_id>/run", methods=["POST"])
@login_required
def job_run(job_id):
    job = _own_job_or_404(job_id)
    if not job["enabled"]:
        flash("Ce job est désactivé (droits insuffisants) — modifiez-le pour le réactiver.", "error")
        return redirect(url_for("sync.dashboard"))
    sync.run_async(job_id, actor=g.user["username"])
    flash(f"Job « {job['name']} » lancé en arrière-plan — suivez sa progression ci-dessous.", "success")
    return redirect(url_for("sync.dashboard"))
