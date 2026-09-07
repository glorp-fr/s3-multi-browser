"""Config backup (Administration > Sauvegarde).

Bundles data/db.json + data/audit.jsonl(.1) into a timestamped .tar.gz and ships it to
either an S3 bucket or an SMB share, then prunes the target to the configured retention.
A background timer re-fires it on the configured schedule; the admin page also triggers
it synchronously via "Sauvegarder maintenant".

Times are UTC (the server runs in UTC, like the audit log).
"""
import io
import os
import tarfile
import threading
from datetime import datetime, timedelta, timezone

from . import audit, storage
from .storage import DATA_DIR

ARCHIVE_PREFIX = "config-backup-"
ARCHIVE_SUFFIX = ".tar.gz"

# Files (relative to DATA_DIR) put in the archive when present.
_MEMBERS = ("db.json", "audit.jsonl", "audit.jsonl.1")


# --- archive ---------------------------------------------------------------

def _build_archive():
    """(filename, bytes) for a fresh gzip tarball of the current config files."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"{ARCHIVE_PREFIX}{ts}{ARCHIVE_SUFFIX}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for member in _MEMBERS:
            path = os.path.join(DATA_DIR, member)
            if os.path.isfile(path):
                tar.add(path, arcname=member)
    return name, buf.getvalue()


def _is_archive(name):
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.startswith(ARCHIVE_PREFIX) and base.endswith(ARCHIVE_SUFFIX)


def _extras(sorted_names, retention):
    """Names to delete so that at most `retention` remain (oldest first — the archive
    timestamp in the name makes a lexical sort chronological)."""
    return sorted_names[:-retention] if len(sorted_names) > retention else []


# --- S3 target -----------------------------------------------------------------

def _s3_client(cfg):
    import boto3

    s3 = cfg["s3"]
    if not (s3["endpoint"] and s3["access_key"] and s3["bucket"]):
        raise ValueError("Configuration S3 incomplète (endpoint, access key, bucket)")
    return boto3.client(
        "s3",
        endpoint_url=s3["endpoint"],
        aws_access_key_id=s3["access_key"],
        aws_secret_access_key=storage.backup_s3_secret_key(cfg) or None,
        region_name=s3["region"] or None,
    )


def _s3_prefix(cfg):
    prefix = (cfg["s3"]["prefix"] or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


def _push_s3(cfg, name, blob):
    client = _s3_client(cfg)
    prefix = _s3_prefix(cfg)
    client.put_object(Bucket=cfg["s3"]["bucket"], Key=prefix + name, Body=blob)
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=cfg["s3"]["bucket"], Prefix=prefix):
        keys.extend(o["Key"] for o in page.get("Contents", []) if _is_archive(o["Key"]))
    extras = _extras(sorted(keys), cfg["retention"])
    if extras:
        client.delete_objects(Bucket=cfg["s3"]["bucket"],
                              Delete={"Objects": [{"Key": k} for k in extras]})
    return f"s3://{cfg['s3']['bucket']}/{prefix}", len(extras)


# --- SMB target --------------------------------------------------------------

def _smb(cfg):
    """Register a session and return (smbclient module, target directory UNC path)."""
    import smbclient

    smb = cfg["smb"]
    if not (smb["server"] and smb["share"]):
        raise ValueError("Configuration SMB incomplète (serveur, partage)")
    user = smb["username"]
    if user and smb["domain"]:
        user = f"{smb['domain']}\\{user}"
    smbclient.register_session(
        smb["server"], username=user or None,
        password=storage.backup_smb_password(cfg) or None,
    )
    unc = rf"\\{smb['server']}\{smb['share']}"
    sub = (smb["path"] or "").strip("/\\").replace("/", "\\")
    return smbclient, (unc + "\\" + sub if sub else unc), unc, sub


def _push_smb(cfg, name, blob):
    sc, target_dir, unc, sub = _smb(cfg)
    if sub:
        acc = unc
        for part in sub.split("\\"):
            acc += "\\" + part
            try:
                sc.mkdir(acc)
            except Exception:  # noqa: BLE001 - already exists / race
                pass
    with sc.open_file(target_dir + "\\" + name, mode="wb") as fh:
        fh.write(blob)
    names = sorted(n for n in sc.listdir(target_dir) if _is_archive(n))
    pruned = 0
    for n in _extras(names, cfg["retention"]):
        try:
            sc.remove(target_dir + "\\" + n)
            pruned += 1
        except Exception:  # noqa: BLE001
            pass
    return target_dir, pruned


# --- run --------------------------------------------------------------------

_run_lock = threading.Lock()


def run_backup(*, actor="scheduler"):
    """Build + ship one archive. Never raises — returns {ok, message, ...}."""
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "message": "Une sauvegarde est déjà en cours."}
    try:
        cfg = storage.get_backup_config()
        name, blob = _build_archive()
        if cfg["destination"] == "smb":
            where, pruned = _push_smb(cfg, name, blob)
        else:
            where, pruned = _push_s3(cfg, name, blob)
        storage.record_backup_result(status="ok", archive=name)
        detail = f"{len(blob)} octets" + (f", {pruned} ancienne(s) purgée(s)" if pruned else "")
        audit.log("admin", "backup_run",
                  f"Sauvegarde config → {where} ({name}, {detail})",
                  actor=actor, target=name)
        return {"ok": True, "archive": name, "bytes": len(blob), "pruned": pruned,
                "message": f"Sauvegarde envoyée vers {where} : {name} ({detail})."}
    except Exception as exc:  # noqa: BLE001 - surface any failure, keep the app alive
        storage.record_backup_result(status="fail", error=str(exc))
        audit.log("admin", "backup_run", f"Échec de la sauvegarde config : {exc}",
                  actor=actor, status="fail")
        return {"ok": False, "message": f"Échec de la sauvegarde : {exc}"}
    finally:
        _run_lock.release()


# --- scheduler ------------------------------------------------------------------

_timer = None
_timer_lock = threading.Lock()


def _next_run_at(cfg, now=None):
    now = now or datetime.now(timezone.utc)
    target = now.replace(hour=int(cfg["hour"]), minute=int(cfg["minute"]),
                         second=0, microsecond=0)
    if cfg["frequency"] == "weekly":
        target += timedelta(days=(int(cfg["weekday"]) - now.weekday()) % 7)
        if target <= now:
            target += timedelta(days=7)
    elif target <= now:
        target += timedelta(days=1)
    return target


def next_run_display():
    cfg = storage.get_backup_config()
    if not cfg["enabled"]:
        return None
    return _next_run_at(cfg).strftime("%d/%m/%Y %H:%M UTC")


def _tick():
    try:
        if storage.get_backup_config()["enabled"]:
            run_backup(actor="scheduler")
    finally:
        reschedule()


def reschedule():
    """(Re)arm the timer from the current config. Safe to call repeatedly."""
    global _timer
    with _timer_lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        cfg = storage.get_backup_config()
        if not cfg["enabled"]:
            return
        delay = max(1.0, (_next_run_at(cfg) - datetime.now(timezone.utc)).total_seconds())
        _timer = threading.Timer(delay, _tick)
        _timer.daemon = True
        _timer.start()


def start():
    """Called once from create_app(). With more than one gunicorn worker each process
    would run its own timer (and thus its own backup) — keep --workers 1."""
    reschedule()
