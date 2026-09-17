"""Graphical editor for bucket-level S3 settings: versioning, object lock default
retention, lifecycle rules, bucket policy (JSON) and ACL. Each section is edited and
undone independently — see `app/bucket_config.py` for the get/set/restore symmetry that
makes the one-level undo possible."""
import json

from botocore.exceptions import ClientError
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import audit, bucket_config, storage
from ..auth import can, can_access_account, login_required
from ..s3client import get_client

bp = Blueprint("bucket_config", __name__)

SECTIONS = ("versioning", "lock", "encryption", "lifecycle", "policy", "acl")


def _get_authorized_account(account_id):
    account = storage.get_account_by_id(account_id)
    if not account or not can_access_account(g.user, account_id):
        abort(404)
    return account


def _require_bucket_admin(account_id):
    if not can(g.user, account_id, "bucket_admin"):
        abort(403)


def _back(account_id, bucket):
    return redirect(url_for("bucket_config.config", account_id=account_id, bucket=bucket))


def _snapshot(account_id, bucket, section):
    snap = storage.get_bucket_config_snapshot(account_id, bucket, section)
    return snap


def _save_snapshot(account_id, bucket, section, before_value):
    storage.save_bucket_config_snapshot(account_id, bucket, section, before_value, g.user["id"])


@bp.route("/accounts/<account_id>/buckets/<bucket>/config")
@login_required
def config(account_id, bucket):
    account = _get_authorized_account(account_id)
    _require_bucket_admin(account_id)
    client = get_client(account)

    state = {}
    errors = {}
    for section, loader in (
        ("versioning", lambda: bucket_config.get_versioning(client, bucket)),
        ("lock", lambda: bucket_config.get_object_lock(client, bucket)),
        ("encryption", lambda: bucket_config.get_encryption(client, bucket)),
        ("lifecycle", lambda: bucket_config.get_lifecycle(client, bucket)),
        ("policy", lambda: bucket_config.get_policy(client, bucket)),
        ("acl", lambda: bucket_config.get_acl(client, bucket)),
    ):
        try:
            state[section] = loader()
        except ClientError as exc:
            state[section] = None
            errors[section] = str(exc)

    policy_pretty = None
    if state.get("policy"):
        try:
            policy_pretty = json.dumps(json.loads(state["policy"]), indent=2, ensure_ascii=False)
        except ValueError:
            policy_pretty = state["policy"]

    snapshots = {s: _snapshot(account_id, bucket, s) for s in SECTIONS}
    acl_detected = bucket_config.detect_canned_acl(state["acl"]) if state.get("acl") else None

    return render_template(
        "bucket_config.html", account=account, bucket=bucket, state=state, errors=errors,
        policy_pretty=policy_pretty, snapshots=snapshots, canned_acls=bucket_config.CANNED_ACLS,
        public_acls=bucket_config.PUBLIC_ACLS, acl_detected=acl_detected,
        grant_permissions=bucket_config.GRANT_PERMISSIONS,
    )


@bp.route("/accounts/<account_id>/buckets/<bucket>/config/versioning", methods=["POST"])
@login_required
def versioning(account_id, bucket):
    account = _get_authorized_account(account_id)
    _require_bucket_admin(account_id)
    status = request.form.get("status")
    client = get_client(account)
    try:
        before = bucket_config.get_versioning(client, bucket)
        bucket_config.set_versioning(client, bucket, status)
        _save_snapshot(account_id, bucket, "versioning", before)
        audit.log("s3_write", "bucket_versioning_set",
                  f"Versionning « {status} » — {account['name']}/{bucket}",
                  target=f"{account['name']}/{bucket}")
        flash("Versionning mis à jour", "success")
    except (ClientError, ValueError) as exc:
        flash(f"Erreur : {exc}", "error")
    return _back(account_id, bucket)


@bp.route("/accounts/<account_id>/buckets/<bucket>/config/lock", methods=["POST"])
@login_required
def lock(account_id, bucket):
    account = _get_authorized_account(account_id)
    _require_bucket_admin(account_id)
    client = get_client(account)
    rule = None
    if request.form.get("rule_enabled"):
        mode = request.form.get("mode", "GOVERNANCE")
        unit = request.form.get("unit", "days")
        value = request.form.get("value", "").strip()
        if not value.isdigit() or int(value) <= 0:
            flash("Durée de rétention par défaut invalide", "error")
            return _back(account_id, bucket)
        rule = {"mode": mode, ("days" if unit == "days" else "years"): int(value)}
    try:
        before = bucket_config.get_object_lock(client, bucket)
        bucket_config.set_object_lock_rule(client, bucket, rule)
        _save_snapshot(account_id, bucket, "lock", before)
        audit.log("s3_write", "bucket_lock_set",
                  f"Rétention par défaut du verrouillage mise à jour — {account['name']}/{bucket}",
                  target=f"{account['name']}/{bucket}")
        flash("Verrouillage (rétention par défaut) mis à jour", "success")
    except ClientError as exc:
        flash(f"Erreur : {exc}", "error")
    return _back(account_id, bucket)


@bp.route("/accounts/<account_id>/buckets/<bucket>/config/encryption", methods=["POST"])
@login_required
def encryption(account_id, bucket):
    account = _get_authorized_account(account_id)
    _require_bucket_admin(account_id)
    client = get_client(account)
    enabled = bool(request.form.get("enabled"))
    try:
        before = bucket_config.get_encryption(client, bucket)
        bucket_config.set_encryption(client, bucket, enabled)
        _save_snapshot(account_id, bucket, "encryption", before)
        audit.log("s3_write", "bucket_encryption_set",
                  f"Chiffrement {'activé' if enabled else 'désactivé'} — {account['name']}/{bucket}",
                  target=f"{account['name']}/{bucket}")
        flash("Chiffrement mis à jour", "success")
    except ClientError as exc:
        flash(f"Erreur : {exc}", "error")
    return _back(account_id, bucket)


def _parse_lifecycle_rules(form):
    rules = []
    ids = form.getlist("rule_id")
    for i, rule_id in enumerate(ids):
        prefix = form.getlist("rule_prefix")[i]
        enabled = form.getlist("rule_enabled")[i] == "1"
        exp = form.getlist("rule_expiration_days")[i].strip()
        noncurrent = form.getlist("rule_noncurrent_days")[i].strip()
        abort_days = form.getlist("rule_abort_multipart_days")[i].strip()
        rules.append({
            "id": rule_id,
            "prefix": prefix,
            "enabled": enabled,
            "expiration_days": int(exp) if exp.isdigit() else None,
            "noncurrent_expiration_days": int(noncurrent) if noncurrent.isdigit() else None,
            "abort_incomplete_multipart_days": int(abort_days) if abort_days.isdigit() else None,
        })
    return rules


@bp.route("/accounts/<account_id>/buckets/<bucket>/config/lifecycle", methods=["POST"])
@login_required
def lifecycle(account_id, bucket):
    account = _get_authorized_account(account_id)
    _require_bucket_admin(account_id)
    client = get_client(account)
    rules = _parse_lifecycle_rules(request.form)
    try:
        before = bucket_config.get_lifecycle(client, bucket)
        bucket_config.set_lifecycle(client, bucket, rules)
        _save_snapshot(account_id, bucket, "lifecycle", before)
        audit.log("s3_write", "bucket_lifecycle_set",
                  f"Lifecycle mis à jour ({len(rules)} règle(s)) — {account['name']}/{bucket}",
                  target=f"{account['name']}/{bucket}")
        flash("Lifecycle mis à jour", "success")
    except ClientError as exc:
        flash(f"Erreur : {exc}", "error")
    return _back(account_id, bucket)


@bp.route("/accounts/<account_id>/buckets/<bucket>/config/policy", methods=["POST"])
@login_required
def policy(account_id, bucket):
    account = _get_authorized_account(account_id)
    _require_bucket_admin(account_id)
    client = get_client(account)
    action = request.form.get("action", "save")
    policy_text = "" if action == "delete" else request.form.get("policy", "")
    try:
        before = bucket_config.get_policy(client, bucket)
        bucket_config.set_policy(client, bucket, policy_text)
        _save_snapshot(account_id, bucket, "policy", before)
        audit.log("s3_write", "bucket_policy_set",
                  f"Bucket policy {'supprimée' if action == 'delete' else 'mise à jour'} — "
                  f"{account['name']}/{bucket}",
                  target=f"{account['name']}/{bucket}")
        flash("Bucket policy supprimée" if action == "delete" else "Bucket policy mise à jour",
              "success")
    except ValueError:
        flash("JSON de policy invalide", "error")
    except ClientError as exc:
        flash(f"Erreur : {exc}", "error")
    return _back(account_id, bucket)


def _parse_account_grants(form):
    grants = []
    types = form.getlist("grant_identifier_type")
    identifiers = form.getlist("grant_identifier")
    permissions = form.getlist("grant_permission")
    for i, identifier in enumerate(identifiers):
        identifier = identifier.strip()
        if not identifier:
            continue
        grants.append({
            "identifier_type": types[i] if i < len(types) else "id",
            "identifier": identifier,
            "permission": permissions[i] if i < len(permissions) else "READ",
        })
    return grants


@bp.route("/accounts/<account_id>/buckets/<bucket>/config/acl", methods=["POST"])
@login_required
def acl(account_id, bucket):
    account = _get_authorized_account(account_id)
    _require_bucket_admin(account_id)
    client = get_client(account)
    canned = request.form.get("canned", "")
    account_grants = _parse_account_grants(request.form)
    if not canned and not account_grants:
        flash("Choisir une ACL prédéfinie ou ajouter au moins un accès par compte avant d'enregistrer", "error")
        return _back(account_id, bucket)
    try:
        before = bucket_config.get_acl(client, bucket)
        bucket_config.apply_acl(client, bucket, before["owner"], canned or None, account_grants)
        _save_snapshot(account_id, bucket, "acl", before)
        audit.log("s3_write", "bucket_acl_set",
                  f"ACL mise à jour ({canned or 'grants personnalisés'}, "
                  f"{len(account_grants)} accès par compte) — {account['name']}/{bucket}",
                  target=f"{account['name']}/{bucket}",
                  status="ok" if canned not in bucket_config.PUBLIC_ACLS else "fail")
        if canned in bucket_config.PUBLIC_ACLS:
            flash(f"ACL « {canned} » appliquée — attention, ce bucket est maintenant public", "error")
        else:
            flash("ACL mise à jour", "success")
    except (ClientError, ValueError) as exc:
        flash(f"Erreur : {exc}", "error")
    return _back(account_id, bucket)


@bp.route("/accounts/<account_id>/buckets/<bucket>/config/<section>/undo", methods=["POST"])
@login_required
def undo(account_id, bucket, section):
    account = _get_authorized_account(account_id)
    _require_bucket_admin(account_id)
    if section not in SECTIONS:
        abort(404)
    snap = _snapshot(account_id, bucket, section)
    if not snap:
        flash("Rien à annuler pour cette section", "error")
        return _back(account_id, bucket)
    client = get_client(account)
    try:
        value = snap["value"]
        if section == "versioning" and not value:
            # Versioning can be suspended but never fully unset once it has been enabled at
            # least once — nothing to replay, but the snapshot is still cleared below.
            flash("Le versionning ne peut pas être ramené à « jamais activé » (limitation S3) ; "
                  "vous pouvez le suspendre manuellement.", "error")
            storage.delete_bucket_config_snapshot(account_id, bucket, section)
            return _back(account_id, bucket)
        if section == "versioning":
            bucket_config.set_versioning(client, bucket, value)
        elif section == "lock":
            bucket_config.set_object_lock_rule(client, bucket, (value or {}).get("rule"))
        elif section == "encryption":
            bucket_config.set_encryption(client, bucket, (value or {}).get("enabled", False))
        elif section == "lifecycle":
            bucket_config.set_lifecycle(client, bucket, value or [])
        elif section == "policy":
            bucket_config.set_policy(client, bucket, value or "")
        elif section == "acl":
            bucket_config.restore_acl(client, bucket, value)
        storage.delete_bucket_config_snapshot(account_id, bucket, section)
        audit.log("s3_write", "bucket_config_undo",
                  f"Annulation « {section} » — {account['name']}/{bucket}",
                  target=f"{account['name']}/{bucket}")
        flash("Modification annulée, configuration précédente restaurée", "success")
    except ClientError as exc:
        flash(f"Erreur lors de l'annulation : {exc}", "error")
    return _back(account_id, bucket)
