import io
import zipfile

from botocore.exceptions import ClientError
from flask import (
    Blueprint, abort, flash, g, redirect, render_template,
    request, send_file, url_for,
)

from .. import audit, storage, usage_cache
from ..auth import accessible_accounts, account_permissions, can, can_access_account, login_required
from ..s3client import get_client

bp = Blueprint("explorer", __name__)

PER_PAGE_OPTIONS = (20, 30, 50, 100)
DEFAULT_PER_PAGE = 20


def _get_authorized_account(account_id):
    account = storage.get_account_by_id(account_id)
    if not account or not can_access_account(g.user, account_id):
        abort(404)
    return account


def _summarize_usage(account_id, bucket_names):
    """Aggregate cached (not live) usage across a set of buckets for one account."""
    total_bytes = 0
    computed_count = 0
    oldest = None
    for name in bucket_names:
        entry = usage_cache.get(account_id, name)
        if entry:
            total_bytes += entry["size_bytes"]
            computed_count += 1
            if oldest is None or entry["computed_at"] < oldest:
                oldest = entry["computed_at"]
    return {
        "total_bytes": total_bytes,
        "bucket_count": len(bucket_names),
        "computed_count": computed_count,
        "oldest_computed_at": oldest,
    }


@bp.route("/")
@login_required
def index():
    return redirect(url_for("explorer.accounts"))


@bp.route("/accounts")
@login_required
def accounts():
    accs = accessible_accounts(g.user)
    providers_by_id = {p["id"]: p for p in storage.get_providers()}
    usage_summaries = {}
    for account in accs:
        try:
            client = get_client(account)
            bucket_names = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
            usage_summaries[account["id"]] = _summarize_usage(account["id"], bucket_names)
        except Exception:
            # A single mis-configured/unreachable account must not break the whole page.
            usage_summaries[account["id"]] = None

    groups = {}
    for account in accs:
        provider = providers_by_id.get(account["provider_id"])
        provider_name = provider["name"] if provider else "Autre"
        groups.setdefault(provider_name, []).append(account)

    return render_template(
        "accounts.html", groups=groups,
        usage_summaries=usage_summaries, providers_by_id=providers_by_id,
    )


@bp.route("/accounts/<account_id>/buckets")
@login_required
def buckets(account_id):
    account = _get_authorized_account(account_id)
    provider = storage.get_provider_by_id(account["provider_id"])
    client = get_client(account)
    try:
        resp = client.list_buckets()
        bucket_list = resp.get("Buckets", [])
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
        bucket_list = []

    audit.log(
        "s3_read", "list_buckets",
        f"Listing des buckets — compte « {account['name']} » ({len(bucket_list)} bucket(s))",
        target=account["name"],
    )

    buckets_view = []
    for b in bucket_list:
        entry = usage_cache.get(account_id, b["Name"])
        buckets_view.append({
            "name": b["Name"],
            "creation_date": b["CreationDate"],
            "usage": entry,
            "refresh_allowed": usage_cache.is_refresh_allowed(entry),
            "next_refresh_at": usage_cache.next_refresh_at(entry),
        })
    usage_summary = _summarize_usage(account_id, [b["Name"] for b in bucket_list])

    return render_template(
        "buckets.html", account=account, provider=provider, buckets=buckets_view,
        usage_summary=usage_summary, perms=account_permissions(g.user, account_id),
    )


@bp.route("/accounts/<account_id>/buckets/new", methods=["POST"])
@login_required
def bucket_new(account_id):
    account = _get_authorized_account(account_id)
    if not can(g.user, account_id, "bucket_admin"):
        abort(403)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Nom de bucket requis", "error")
        return redirect(url_for("explorer.buckets", account_id=account_id))
    client = get_client(account)
    try:
        client.create_bucket(Bucket=name)
        flash(f"Bucket « {name} » créé", "success")
        audit.log("s3_write", "create_bucket",
                  f"Bucket « {name} » créé — compte « {account['name']} »",
                  target=f"{account['name']}/{name}")
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
        audit.log("s3_write", "create_bucket",
                  f"Échec création bucket « {name} » — compte « {account['name']} » : {exc}",
                  target=f"{account['name']}/{name}", status="fail")
    return redirect(url_for("explorer.buckets", account_id=account_id))


@bp.route("/accounts/<account_id>/buckets/<bucket>/delete", methods=["POST"])
@login_required
def bucket_delete(account_id, bucket):
    account = _get_authorized_account(account_id)
    if not can(g.user, account_id, "bucket_admin"):
        abort(403)
    client = get_client(account)
    try:
        client.delete_bucket(Bucket=bucket)
        flash(f"Bucket « {bucket} » supprimé", "success")
        audit.log("s3_write", "delete_bucket",
                  f"Bucket « {bucket} » supprimé — compte « {account['name']} »",
                  target=f"{account['name']}/{bucket}")
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
        audit.log("s3_write", "delete_bucket",
                  f"Échec suppression bucket « {bucket} » — compte « {account['name']} » : {exc}",
                  target=f"{account['name']}/{bucket}", status="fail")
    return redirect(url_for("explorer.buckets", account_id=account_id))


@bp.route("/accounts/<account_id>/buckets/<bucket>/usage/refresh", methods=["POST"])
@login_required
def bucket_usage_refresh(account_id, bucket):
    # Recomputing volumetry is a read (s3_read); any user with access to the account may do it.
    account = _get_authorized_account(account_id)
    entry = usage_cache.get(account_id, bucket)
    if not usage_cache.is_refresh_allowed(entry):
        next_at = usage_cache.next_refresh_at(entry)
        flash(
            f"Volumétrie de « {bucket} » déjà calculée récemment. "
            f"Prochaine actualisation possible à partir du {next_at:%d/%m/%Y %H:%M} UTC.",
            "error",
        )
        return redirect(url_for("explorer.buckets", account_id=account_id))
    client = get_client(account)
    try:
        size_bytes, object_count = usage_cache.compute_bucket_usage(client, bucket)
        usage_cache.set(account_id, bucket, size_bytes, object_count)
        audit.log("s3_read", "usage_refresh",
                  f"Volumétrie recalculée — {account['name']}/{bucket} : "
                  f"{usage_cache.format_size(size_bytes)} ({object_count} objets)",
                  target=f"{account['name']}/{bucket}")
        flash(
            f"Volumétrie de « {bucket} » mise à jour : "
            f"{usage_cache.format_size(size_bytes)} ({object_count} objets)",
            "success",
        )
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
    return redirect(url_for("explorer.buckets", account_id=account_id))


@bp.route("/accounts/<account_id>/buckets/bulk", methods=["POST"])
@login_required
def bucket_bulk(account_id):
    """Grouped action on several buckets: refresh volumetry, or delete."""
    account = _get_authorized_account(account_id)
    action = request.form.get("action")
    names = [n for n in request.form.getlist("bucket") if n]
    back = redirect(url_for("explorer.buckets", account_id=account_id))
    if not names:
        flash("Aucun bucket sélectionné", "error")
        return back
    client = get_client(account)

    if action == "usage_refresh":
        done = skipped = errors = 0
        for name in names:
            entry = usage_cache.get(account_id, name)
            if not usage_cache.is_refresh_allowed(entry):
                skipped += 1
                continue
            try:
                size_bytes, object_count = usage_cache.compute_bucket_usage(client, name)
                usage_cache.set(account_id, name, size_bytes, object_count)
                done += 1
            except ClientError:
                errors += 1
        audit.log("s3_read", "usage_refresh_bulk",
                  f"Volumétrie groupée — {account['name']} : {done} recalculé(s), "
                  f"{skipped} ignoré(s) (<24 h), {errors} en erreur",
                  target=account["name"], status="fail" if errors else "ok")
        msg = f"{done} volumétrie(s) recalculée(s)"
        if skipped:
            msg += f", {skipped} ignorée(s) (calcul de moins de 24 h)"
        if errors:
            msg += f", {errors} en erreur"
        flash(msg, "error" if errors else "success")
        return back

    if action == "delete":
        if not can(g.user, account_id, "bucket_admin"):
            abort(403)
        done = errors = 0
        for name in names:
            try:
                client.delete_bucket(Bucket=name)
                done += 1
            except ClientError:
                errors += 1
        audit.log("s3_write", "delete_bucket_bulk",
                  f"Suppression groupée de buckets — {account['name']} : "
                  f"{done} supprimé(s), {errors} en erreur",
                  target=account["name"], status="fail" if errors else "ok")
        flash(f"{done} bucket(s) supprimé(s)" + (f", {errors} en erreur" if errors else ""),
              "error" if errors else "success")
        return back

    flash("Action groupée inconnue", "error")
    return back


@bp.route("/accounts/<account_id>/buckets/<bucket>")
@login_required
def objects(account_id, bucket):
    account = _get_authorized_account(account_id)
    prefix = request.args.get("prefix", "")
    query = request.args.get("q", "").strip()

    try:
        per_page = int(request.args.get("per_page", DEFAULT_PER_PAGE))
    except ValueError:
        per_page = DEFAULT_PER_PAGE
    if per_page not in PER_PAGE_OPTIONS:
        per_page = DEFAULT_PER_PAGE

    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    page = max(page, 1)

    client = get_client(account)
    folders, files = [], []
    try:
        # Search/pagination stay scoped to the current folder (not a recursive whole-bucket
        # scan) so a single request stays bounded to this prefix's listing.
        paginator = client.get_paginator("list_objects_v2")
        for result_page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
            for common in result_page.get("CommonPrefixes", []):
                full = common["Prefix"]
                name = full[len(prefix):].rstrip("/")
                if not query or query.lower() in name.lower():
                    folders.append({"prefix": full, "name": name})
            for obj in result_page.get("Contents", []):
                if obj["Key"] == prefix:
                    continue
                name = obj["Key"][len(prefix):]
                if not query or query.lower() in name.lower():
                    files.append({
                        "key": obj["Key"],
                        "name": name,
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    })
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")

    audit.log(
        "s3_read", "list_objects",
        f"Navigation — {account['name']}/{bucket}/{prefix}"
        + (f" (recherche « {query} »)" if query else ""),
        target=f"{account['name']}/{bucket}/{prefix}",
    )

    total_files = len(files)
    total_pages = max(1, -(-total_files // per_page))
    page = min(page, total_pages)
    files_page = files[(page - 1) * per_page: page * per_page]

    breadcrumbs = []
    parts = [p for p in prefix.split("/") if p]
    acc = ""
    for part in parts:
        acc += part + "/"
        breadcrumbs.append((part, acc))

    return render_template(
        "explorer.html",
        account=account, bucket=bucket, prefix=prefix, query=query,
        folders=folders, files=files_page, breadcrumbs=breadcrumbs,
        perms=account_permissions(g.user, account_id),
        page=page, per_page=per_page, total_pages=total_pages, total_files=total_files,
        per_page_options=PER_PAGE_OPTIONS,
    )


@bp.route("/accounts/<account_id>/buckets/<bucket>/upload", methods=["POST"])
@login_required
def upload(account_id, bucket):
    account = _get_authorized_account(account_id)
    if not can(g.user, account_id, "upload"):
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
            audit.log("s3_write", "upload",
                      f"Échec upload « {file.filename} » — {account['name']}/{bucket}/{prefix} : {exc}",
                      target=f"{account['name']}/{bucket}/{key}", status="fail")
    if uploaded:
        flash(f"{uploaded} fichier(s) envoyé(s)", "success")
        audit.log("s3_write", "upload",
                  f"{uploaded} fichier(s) envoyé(s) — {account['name']}/{bucket}/{prefix}",
                  target=f"{account['name']}/{bucket}/{prefix}")
    return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket, prefix=prefix))


@bp.route("/accounts/<account_id>/buckets/<bucket>/mkdir", methods=["POST"])
@login_required
def mkdir(account_id, bucket):
    account = _get_authorized_account(account_id)
    if not can(g.user, account_id, "upload"):
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
        audit.log("s3_write", "mkdir",
                  f"Dossier « {name} » créé — {account['name']}/{bucket}/{prefix}",
                  target=f"{account['name']}/{bucket}/{prefix}{name}/")
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
        audit.log("s3_write", "mkdir",
                  f"Échec création dossier « {name} » — {account['name']}/{bucket}/{prefix} : {exc}",
                  target=f"{account['name']}/{bucket}/{prefix}{name}/", status="fail")
    return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket, prefix=prefix))


@bp.route("/accounts/<account_id>/buckets/<bucket>/download")
@login_required
def download(account_id, bucket):
    account = _get_authorized_account(account_id)
    if not can(g.user, account_id, "download"):
        abort(403)
    key = request.args.get("key", "")
    client = get_client(account)
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
        return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket))
    audit.log("s3_read", "download",
              f"Téléchargement — {account['name']}/{bucket}/{key}",
              target=f"{account['name']}/{bucket}/{key}")
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
    if not can(g.user, account_id, "delete"):
        abort(403)
    key = request.form.get("key", "")
    prefix = request.form.get("prefix", "")
    client = get_client(account)
    is_folder = key.endswith("/")
    action = "delete_folder" if is_folder else "delete_object"
    kind = "Dossier" if is_folder else "Objet"
    try:
        if is_folder:
            # "Folder": delete every object under this prefix.
            paginator = client.get_paginator("list_objects_v2")
            to_delete = []
            for page in paginator.paginate(Bucket=bucket, Prefix=key):
                to_delete.extend({"Key": o["Key"]} for o in page.get("Contents", []))
            for i in range(0, len(to_delete), 1000):
                client.delete_objects(Bucket=bucket, Delete={"Objects": to_delete[i:i + 1000]})
            detail = f" ({len(to_delete)} objet(s))"
        else:
            client.delete_object(Bucket=bucket, Key=key)
            detail = ""
        flash("Suppression effectuée", "success")
        audit.log("s3_write", action,
                  f"{kind} supprimé — {account['name']}/{bucket}/{key}{detail}",
                  target=f"{account['name']}/{bucket}/{key}")
    except ClientError as exc:
        flash(f"Erreur OOS : {exc}", "error")
        audit.log("s3_write", action,
                  f"Échec suppression — {account['name']}/{bucket}/{key} : {exc}",
                  target=f"{account['name']}/{bucket}/{key}", status="fail")
    return redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket, prefix=prefix))


def _expand_keys(client, bucket, keys):
    """Expand a mix of object keys and "folder" prefixes (trailing /) into a flat,
    deduplicated, sorted list of real object keys."""
    out = []
    for key in keys:
        if key.endswith("/"):
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=key):
                out.extend(o["Key"] for o in page.get("Contents", []) if not o["Key"].endswith("/"))
        else:
            out.append(key)
    return sorted(set(out))


@bp.route("/accounts/<account_id>/buckets/<bucket>/bulk", methods=["POST"])
@login_required
def object_bulk(account_id, bucket):
    """Grouped action on selected objects/folders: delete, or download as a .zip."""
    account = _get_authorized_account(account_id)
    action = request.form.get("action")
    prefix = request.form.get("prefix", "")
    keys = [k for k in request.form.getlist("key") if k]
    back = redirect(url_for("explorer.objects", account_id=account_id, bucket=bucket, prefix=prefix))
    if not keys:
        flash("Aucun élément sélectionné", "error")
        return back
    client = get_client(account)

    if action == "delete":
        if not can(g.user, account_id, "delete"):
            abort(403)
        deleted = errors = 0
        try:
            batch = [{"Key": k} for k in _expand_keys(client, bucket, keys)]
            for i in range(0, len(batch), 1000):
                resp = client.delete_objects(Bucket=bucket, Delete={"Objects": batch[i:i + 1000]})
                deleted += len(resp.get("Deleted", []))
                errors += len(resp.get("Errors", []))
        except ClientError as exc:
            flash(f"Erreur OOS : {exc}", "error")
            errors += 1
        audit.log("s3_write", "delete_object_bulk",
                  f"Suppression groupée — {account['name']}/{bucket}/{prefix} : "
                  f"{len(keys)} sélection(s) → {deleted} objet(s) supprimé(s)"
                  + (f", {errors} erreur(s)" if errors else ""),
                  target=f"{account['name']}/{bucket}/{prefix}", status="fail" if errors else "ok")
        flash(f"{deleted} objet(s) supprimé(s)" + (f", {errors} erreur(s)" if errors else ""),
              "error" if errors else "success")
        return back

    if action == "download":
        if not can(g.user, account_id, "download"):
            abort(403)
        try:
            obj_keys = _expand_keys(client, bucket, keys)
        except ClientError as exc:
            flash(f"Erreur OOS : {exc}", "error")
            return back
        if not obj_keys:
            flash("Aucun objet à télécharger dans la sélection", "error")
            return back
        buf = io.BytesIO()
        added = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for key in obj_keys:
                try:
                    obj = client.get_object(Bucket=bucket, Key=key)
                except ClientError:
                    continue
                arcname = key[len(prefix):] if prefix and key.startswith(prefix) else key
                zf.writestr(arcname or key.rsplit("/", 1)[-1], obj["Body"].read())
                added += 1
        buf.seek(0)
        audit.log("s3_read", "download_zip",
                  f"Téléchargement .zip — {account['name']}/{bucket}/{prefix} : {added} objet(s)",
                  target=f"{account['name']}/{bucket}/{prefix}")
        zipname = (prefix.rstrip("/").rsplit("/", 1)[-1] or bucket) + ".zip"
        return send_file(buf, as_attachment=True, download_name=zipname, mimetype="application/zip")

    flash("Action groupée inconnue", "error")
    return back
