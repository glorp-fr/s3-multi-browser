"""Cross-account bucket sync / copy engine (Synchronisation).

A "sync job" moves objects from a source (one object, a prefix, a whole bucket, or an
explicit key selection) to a destination prefix on another bucket — possibly on a
different account, even a different provider. There is no server-side CopyObject between
two different S3 endpoints, so every object streams through this process: `get_object`
on the source, `upload_fileobj` (chunked, never the whole object buffered) on the
destination.

A job either runs once on demand (`run_async`, used by the "Lancer maintenant" button and
by the explorer's "Copier vers…" bulk action) or recurs on a schedule (same
daily/weekly/hour/minute shape as `backup.py`, one `threading.Timer` per job). Like
`backup.py`, this assumes a single, multi-threaded gunicorn worker — with more than one
worker each process would run its own timers.

Permissions are re-checked at every run, not just at creation: if the job's owner has
since lost `download` on the source account or `upload` (or `delete`, when
`delete_extraneous` is set) on the destination account, the job is disabled automatically
(`status.state = "rights_error"`) instead of silently running with stale rights. An admin
can then re-enable it by reassigning it to themselves (`storage.reassign_sync_job`) —
admins hold every capability by construction, so that always clears the check.
"""
import threading
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from . import audit, auth, storage
from .s3client import get_client

# In-memory only — never persisted. Lost on restart like `backup._timer`; a job caught
# mid-run at that point just looks idle again afterwards, nothing worse.
_progress = {}
_running = set()
_running_lock = threading.Lock()

_timers = {}
_timers_lock = threading.Lock()


def _acquire(job_id):
    with _running_lock:
        if job_id in _running:
            return False
        _running.add(job_id)
        return True


def _release(job_id):
    with _running_lock:
        _running.discard(job_id)


def get_progress(job_id):
    """{"done", "total", "bytes"} while `job_id` is running, else None."""
    return _progress.get(job_id)


def _account_label(account_id):
    account = storage.get_account_by_id(account_id)
    return account["name"] if account else account_id


def _job_label(job):
    return (f"{job['name']} ({_account_label(job['source']['account_id'])}/{job['source']['bucket']}"
            f" → {_account_label(job['dest']['account_id'])}/{job['dest']['bucket']})")


# --- rights ------------------------------------------------------------------

def _check_rights(job):
    owner = storage.get_user_by_id(job["owner_id"])
    if not owner:
        return None, "Propriétaire du job introuvable."
    if not storage.get_account_by_id(job["source"]["account_id"]):
        return owner, "Compte source introuvable."
    if not storage.get_account_by_id(job["dest"]["account_id"]):
        return owner, "Compte destination introuvable."
    if not auth.can(owner, job["source"]["account_id"], "download"):
        return owner, f"« {owner['username']} » n'a plus le droit de téléchargement sur le compte source."
    if not auth.can(owner, job["dest"]["account_id"], "upload"):
        return owner, f"« {owner['username']} » n'a plus le droit de dépôt sur le compte destination."
    if job["delete_extraneous"] and not auth.can(owner, job["dest"]["account_id"], "delete"):
        return owner, f"« {owner['username']} » n'a plus le droit de suppression sur le compte destination."
    return owner, None


# --- source resolution -------------------------------------------------------

def _resolve_keys(client, bucket, source):
    """[(source_key, relative_path)], relative_path used to build the destination key."""
    scope = source["scope"]
    if scope == "object":
        key = source["value"]
        return [(key, key.rsplit("/", 1)[-1])]
    if scope == "selection":
        base = source.get("value") or ""
        out = []
        for key in source.get("keys") or []:
            rel = key[len(base):] if base and key.startswith(base) else key.rsplit("/", 1)[-1]
            out.append((key, rel))
        return out
    # "prefix" or "bucket" — a live listing (not the caller's belief about what's there).
    prefix = source["value"] if scope == "prefix" else ""
    out = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue  # "folder marker" objects — nothing to copy
            rel = key[len(prefix):] if prefix else key
            out.append((key, rel))
    return out


def _delete_extraneous(dst_client, job, kept_rel):
    prefix = job["dest"]["prefix"]
    to_delete = []
    paginator = dst_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=job["dest"]["bucket"], Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            rel = key[len(prefix):] if prefix else key
            if rel not in kept_rel:
                to_delete.append({"Key": key})
    deleted = 0
    for i in range(0, len(to_delete), 1000):
        resp = dst_client.delete_objects(Bucket=job["dest"]["bucket"], Delete={"Objects": to_delete[i:i + 1000]})
        deleted += len(resp.get("Deleted", []))
    return deleted


def _is_missing(exc):
    code = exc.response.get("Error", {}).get("Code", "")
    return code in ("404", "NoSuchKey", "NotFound")


# --- run ----------------------------------------------------------------------

def run_sync_job(job_id, *, actor=None):
    """Run one job to completion. Never raises — returns {ok, message}."""
    if not _acquire(job_id):
        return {"ok": False, "message": "Ce job est déjà en cours d'exécution."}
    try:
        job = storage.get_sync_job_by_id(job_id)
        if not job:
            return {"ok": False, "message": "Job introuvable."}
        if not job.get("enabled"):
            return {"ok": False, "message": "Job désactivé."}

        owner, rights_error = _check_rights(job)
        if rights_error:
            storage.set_sync_job_enabled(job_id, False, state="rights_error")
            storage.record_sync_job_result(job_id, state="rights_error", summary=rights_error)
            audit.log("s3_write", "sync_job_disabled",
                      f"Job « {job['name']} » désactivé automatiquement : {rights_error}",
                      actor=actor or "scheduler", target=job["name"], status="fail")
            return {"ok": False, "message": rights_error}

        _progress[job_id] = {"done": 0, "total": 0, "bytes": 0}
        try:
            src_account = storage.get_account_by_id(job["source"]["account_id"])
            dst_account = storage.get_account_by_id(job["dest"]["account_id"])
            src_client = get_client(src_account)
            dst_client = get_client(dst_account)

            keys = _resolve_keys(src_client, job["source"]["bucket"], job["source"])
            _progress[job_id]["total"] = len(keys)

            copied = skipped = errors = 0
            transferred_bytes = 0
            kept_rel = set()

            for key, rel in keys:
                kept_rel.add(rel)
                dest_key = job["dest"]["prefix"] + rel
                try:
                    src_head = src_client.head_object(Bucket=job["source"]["bucket"], Key=key)
                except ClientError:
                    errors += 1
                    _progress[job_id]["done"] += 1
                    continue

                dst_head = None
                try:
                    dst_head = dst_client.head_object(Bucket=job["dest"]["bucket"], Key=dest_key)
                except ClientError as exc:
                    if not _is_missing(exc):
                        errors += 1
                        _progress[job_id]["done"] += 1
                        continue

                up_to_date = (
                    dst_head is not None
                    and dst_head.get("ContentLength") == src_head.get("ContentLength")
                    and dst_head.get("ETag") == src_head.get("ETag")
                )
                if up_to_date:
                    skipped += 1
                else:
                    try:
                        obj = src_client.get_object(Bucket=job["source"]["bucket"], Key=key)
                        dst_client.upload_fileobj(obj["Body"], job["dest"]["bucket"], dest_key)
                        copied += 1
                        transferred_bytes += src_head.get("ContentLength") or 0
                    except ClientError:
                        errors += 1
                _progress[job_id]["done"] += 1
                _progress[job_id]["bytes"] = transferred_bytes

            deleted = 0
            if job["delete_extraneous"]:
                deleted = _delete_extraneous(dst_client, job, kept_rel)

            state = "error" if errors else "done"
            summary = f"{copied} copié(s), {skipped} déjà à jour"
            if job["delete_extraneous"]:
                summary += f", {deleted} supprimé(s) à destination"
            if errors:
                summary += f", {errors} erreur(s)"

            storage.record_sync_job_result(job_id, state=state, summary=summary)
            audit.log("s3_write", "sync_run", f"Sync {_job_label(job)} : {summary}",
                      actor=actor or (owner["username"] if owner else "scheduler"),
                      target=job["name"], status="fail" if errors else "ok")
            return {"ok": errors == 0, "message": summary}
        except Exception as exc:  # noqa: BLE001 - surface any failure, keep the app alive
            storage.record_sync_job_result(job_id, state="error", summary=str(exc))
            audit.log("s3_write", "sync_run", f"Échec sync {_job_label(job)} : {exc}",
                      actor=actor or (owner["username"] if owner else "scheduler"),
                      target=job["name"], status="fail")
            return {"ok": False, "message": str(exc)}
        finally:
            _progress.pop(job_id, None)
    finally:
        _release(job_id)


def run_async(job_id, *, actor=None):
    """Fire-and-forget: used by "Lancer maintenant" and the explorer's "Copier vers…"."""
    thread = threading.Thread(target=run_sync_job, args=(job_id,), kwargs={"actor": actor}, daemon=True)
    thread.start()


# --- scheduler ------------------------------------------------------------------

def _next_run_at(sched, now=None):
    now = now or datetime.now(timezone.utc)
    target = now.replace(hour=int(sched["hour"]), minute=int(sched["minute"]),
                         second=0, microsecond=0)
    if sched["frequency"] == "weekly":
        target += timedelta(days=(int(sched["weekday"]) - now.weekday()) % 7)
        if target <= now:
            target += timedelta(days=7)
    elif target <= now:
        target += timedelta(days=1)
    return target


def next_run_display(job):
    sched = job["schedule"]
    if not job.get("enabled") or not sched.get("enabled"):
        return None
    return _next_run_at(sched).strftime("%d/%m/%Y %H:%M UTC")


def _tick(job_id):
    try:
        job = storage.get_sync_job_by_id(job_id)
        if job and job.get("enabled") and job["schedule"].get("enabled"):
            run_sync_job(job_id, actor="scheduler")
    finally:
        reschedule(job_id)


def reschedule(job_id):
    """(Re)arm the timer for one job from its current config. Safe to call repeatedly."""
    with _timers_lock:
        old = _timers.pop(job_id, None)
        if old is not None:
            old.cancel()
        job = storage.get_sync_job_by_id(job_id)
        if not job or not job.get("enabled") or not job["schedule"].get("enabled"):
            return
        delay = max(1.0, (_next_run_at(job["schedule"]) - datetime.now(timezone.utc)).total_seconds())
        timer = threading.Timer(delay, _tick, args=(job_id,))
        timer.daemon = True
        timer.start()
        _timers[job_id] = timer


def cancel(job_id):
    with _timers_lock:
        old = _timers.pop(job_id, None)
        if old is not None:
            old.cancel()


def reschedule_all():
    for job in storage.get_sync_jobs():
        reschedule(job["id"])


def start():
    """Called once from create_app(). Mono-worker gunicorn required (see backup.py) —
    with several workers each process would arm its own timers for the same jobs."""
    reschedule_all()
