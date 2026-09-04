from botocore.exceptions import ClientError
from flask import (
    Blueprint, abort, flash, g, redirect, render_template,
    request, send_file, url_for,
)
import io

from .. import storage
from ..auth import accessible_accounts, can_access_account, can_write, login_required
from ..s3client import get_client

bp = Blueprint("explorer", __name__)


def _get_authorized_account(account_id):
    account = storage.get_account_by_id(account_id)
    if not account or not can_access_account(g.user, account_id):
        abort(404)
    return account


@bp.route("/")
@login_required
def index():
    return redirect(url_for("explorer.accounts"))


@bp.route("/accounts")
@login_required
def accounts():
    return render_template("accounts.html", accounts=accessible_accounts(g.user))


@bp.route("/accounts/<account_id>/buckets")
@login_required
def buckets(account_id):
    account = _get_authorized_account(account_id)
    client = get_client(account)
    try:
        resp = client.list_buckets()
        bucket_list = resp.get("Buckets", [])
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
        bucket_list = []
    return render_template(
        "buckets.html", account=account, buckets=bucket_list, can_write=can_write(g.user)
    )


@bp.route("/accounts/<account_id>/buckets/new", methods=["POST"])
@login_required
def bucket_new(account_id):
    account = _get_authorized_account(account_id)
    if not can_write(g.user):
        abort(403)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Nom de bucket requis", "error")
        return redirect(url_for("explorer.buckets", account_id=account_id))
    client = get_client(account)
    try:
        client.create_bucket(Bucket=name)
        flash(f"Bucket « {name} » créé", "success")
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
    return redirect(url_for("explorer.buckets", account_id=account_id))


@bp.route("/accounts/<account_id>/buckets/<bucket>/delete", methods=["POST"])
@login_required
def bucket_delete(account_id, bucket):
    account = _get_authorized_account(account_id)
    if not can_write(g.user):
        abort(403)
    client = get_client(account)
    try:
        client.delete_bucket(Bucket=bucket)
        flash(f"Bucket « {bucket} » supprimé", "success")
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
    return redirect(url_for("explorer.buckets", account_id=account_id))


@bp.route("/accounts/<account_id>/buckets/<bucket>")
@login_required
def objects(account_id, bucket):
    account = _get_authorized_account(account_id)
    prefix = request.args.get("prefix", "")
    client = get_client(account)
    folders, files = [], []
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
            for common in page.get("CommonPrefixes", []):
                full = common["Prefix"]
                folders.append({"prefix": full, "name": full[len(prefix):].rstrip("/")})
            for obj in page.get("Contents", []):
                if obj["Key"] == prefix:
                    continue
                files.append({
                    "key": obj["Key"],
                    "name": obj["Key"][len(prefix):],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"],
                })
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")

    breadcrumbs = []
    parts = [p for p in prefix.split("/") if p]
    acc = ""
    for part in parts:
        acc += part + "/"
        breadcrumbs.append((part, acc))

    return render_template(
        "explorer.html",
        account=account, bucket=bucket, prefix=prefix,
        folders=folders, files=files, breadcrumbs=breadcrumbs,
        can_write=can_write(g.user),
    )


@bp.route("/accounts/<account_id>/buckets/<bucket>/upload", methods=["POST"])
@login_required
def upload(account_id, bucket):
    account = _get_authorized_account(account_id)
    if not can_write(g.user):
        abort(403)
    prefix = request.form.get("prefix", "")
    client = get_client(account)
    uploaded = 0
    for file in request.files.getlist("files"):
        if not file or not file.filename:
            continue
        key = prefix + file.filename
        try:
            client.upload_fileobj(file.stream, bucket, key)
            uploaded += 1
        except ClientError as exc:
            flash(f"Erreur lors de l'upload de {file.filename} : {exc}", "error")
    if uploaded:
        flash(f"{uploaded} fichier(s) envoyé(s)", "success")
    return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket, prefix=prefix))


@bp.route("/accounts/<account_id>/buckets/<bucket>/mkdir", methods=["POST"])
@login_required
def mkdir(account_id, bucket):
    account = _get_authorized_account(account_id)
    if not can_write(g.user):
        abort(403)
    prefix = request.form.get("prefix", "")
    name = request.form.get("name", "").strip().strip("/")
    if not name:
        flash("Nom de dossier requis", "error")
        return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket, prefix=prefix))
    client = get_client(account)
    try:
        client.put_object(Bucket=bucket, Key=f"{prefix}{name}/", Body=b"")
        flash("Dossier créé", "success")
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
    return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket, prefix=prefix))


@bp.route("/accounts/<account_id>/buckets/<bucket>/download")
@login_required
def download(account_id, bucket):
    account = _get_authorized_account(account_id)
    key = request.args.get("key", "")
    client = get_client(account)
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
        return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket))
    filename = key.rsplit("/", 1)[-1] or key
    return send_file(
        io.BytesIO(obj["Body"].read()),
        as_attachment=True,
        download_name=filename,
        mimetype=obj.get("ContentType", "application/octet-stream"),
    )


@bp.route("/accounts/<account_id>/buckets/<bucket>/delete-object", methods=["POST"])
@login_required
def delete_object(account_id, bucket):
    account = _get_authorized_account(account_id)
    if not can_write(g.user):
        abort(403)
    key = request.form.get("key", "")
    prefix = request.form.get("prefix", "")
    client = get_client(account)
    try:
        if key.endswith("/"):
            # "Folder": delete every object under this prefix.
            paginator = client.get_paginator("list_objects_v2")
            to_delete = []
            for page in paginator.paginate(Bucket=bucket, Prefix=key):
                to_delete.extend({"Key": o["Key"]} for o in page.get("Contents", []))
            for i in range(0, len(to_delete), 1000):
                client.delete_objects(Bucket=bucket, Delete={"Objects": to_delete[i:i + 1000]})
        else:
            client.delete_object(Bucket=bucket, Key=key)
        flash("Suppression effectuée", "success")
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
    return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket, prefix=prefix))
