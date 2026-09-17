"""Append-only audit / activity log. JSON Lines on disk — no database, same spirit
as storage.py / usage_cache.py (lock + plain files).

One line per event. `seq` is a strictly increasing integer used as a cursor by the
live log view (poll `?after=<seq>`). The live file is capped in size; past that it
rotates into a dated archive (`audit.jsonl` -> `audit-YYYYMMDD-HHMMSS.jsonl`) instead
of being overwritten, so history accumulates rather than being lost at each rotation.
Archives older than the configured retention (Administration > Logs) are purged, both
right after a rotation and by a daily background sweep started from `start()`.
"""
import glob
import json
import os
import threading
from datetime import datetime, timezone

from flask import g, has_request_context, request

from . import storage
from .storage import DATA_DIR

LOG_PATH = os.path.join(DATA_DIR, "audit.jsonl")
MAX_BYTES = 5 * 1024 * 1024

ARCHIVE_PREFIX = "audit-"
ARCHIVE_SUFFIX = ".jsonl"
_LEGACY_ROTATED_PATH = LOG_PATH + ".1"  # pre-v0.9.9 single-level rotation

# Categories used across the app. Kept in sync with the filter checkboxes in admin_logs.html.
CATEGORIES = ("auth", "s3_read", "s3_write", "admin")

_lock = threading.Lock()
_seq = 0


def is_log_archive_name(name):
    """True for a rotated-archive basename (`audit-YYYYMMDD-HHMMSS[-N].jsonl`). Used both
    internally and by backup.py to select which files a config backup/restore should carry."""
    return (
        isinstance(name, str) and "/" not in name and "\\" not in name
        and name.startswith(ARCHIVE_PREFIX) and name.endswith(ARCHIVE_SUFFIX)
    )


def list_archive_paths():
    """Absolute paths of every retained rotated archive, unsorted."""
    return glob.glob(os.path.join(DATA_DIR, f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"))


def _latest_archive_path():
    paths = list_archive_paths()
    return max(paths, key=os.path.getmtime) if paths else None


def migrate_legacy_rotation():
    """One-time upgrade: fold the old single-slot `audit.jsonl.1` into the new dated-archive
    scheme, using its mtime as the rotation timestamp, so it shows up in the retained history
    instead of being silently ignored. No-op once already migrated (or if there was nothing
    to migrate)."""
    if not os.path.exists(_LEGACY_ROTATED_PATH):
        return
    try:
        mtime = os.path.getmtime(_LEGACY_ROTATED_PATH)
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(DATA_DIR, f"{ARCHIVE_PREFIX}{ts}{ARCHIVE_SUFFIX}")
        i = 1
        while os.path.exists(dest):
            dest = os.path.join(DATA_DIR, f"{ARCHIVE_PREFIX}{ts}-{i}{ARCHIVE_SUFFIX}")
            i += 1
        os.replace(_LEGACY_ROTATED_PATH, dest)
    except OSError:
        pass


migrate_legacy_rotation()


def _iter_records(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _init_seq():
    # Only the live file + the most recent archive matter for the cursor: `seq` only needs
    # to keep increasing, it doesn't need to reflect every byte ever retained on disk.
    global _seq
    last = 0
    latest = _latest_archive_path()
    for path in ([latest] if latest else []) + [LOG_PATH]:
        if os.path.exists(path):
            for rec in _iter_records(path):
                seq = rec.get("seq")
                if isinstance(seq, int) and seq > last:
                    last = seq
    _seq = last


_init_seq()


def _client_ip():
    if not has_request_context():
        return None
    # No ProxyFix yet (TLS/reverse-proxy is a later step); best effort.
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr


def log(category, action, message, *, actor=None, status="ok", target=None, **extra):
    """Record one event. Never raises — a logging failure must not break a request."""
    global _seq
    if actor is None and has_request_context():
        user = g.get("user")
        actor = user["username"] if user else None

    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "category": category,
        "action": action,
        "message": message,
        "actor": actor or "-",
        "status": status,
    }
    if target:
        rec["target"] = target
    if has_request_context():
        rec["ip"] = _client_ip()
        rec["method"] = request.method
        rec["path"] = request.path
        ua = request.headers.get("User-Agent", "")
        if ua:
            rec["user_agent"] = ua[:300]
    rec.update(extra)

    try:
        with _lock:
            _seq += 1
            rec["seq"] = _seq
            os.makedirs(DATA_DIR, exist_ok=True)
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_BYTES:
                _rotate()
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def _rotate():
    """Move the live file out to a dated archive (never overwritten — history accumulates).
    Called with `_lock` already held."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(DATA_DIR, f"{ARCHIVE_PREFIX}{ts}{ARCHIVE_SUFFIX}")
    i = 1
    while os.path.exists(dest):
        dest = os.path.join(DATA_DIR, f"{ARCHIVE_PREFIX}{ts}-{i}{ARCHIVE_SUFFIX}")
        i += 1
    os.replace(LOG_PATH, dest)
    try:
        purge_expired_archives(storage.get_log_retention()["retention_days"])
    except OSError:
        pass


def purge_expired_archives(retention_days):
    """Delete archived (already-rotated) log files older than `retention_days`. Never
    touches the live audit.jsonl. Returns the number of files removed."""
    if not retention_days or retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    removed = 0
    for path in list_archive_paths():
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def list_log_files():
    """[{name, size, mtime, current}] for the live file + every retained archive, newest
    first — feeds the download list in Administration > Logs."""
    items = []
    if os.path.isfile(LOG_PATH):
        st = os.stat(LOG_PATH)
        items.append({"name": os.path.basename(LOG_PATH), "size": st.st_size,
                      "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc), "current": True})
    for path in list_archive_paths():
        st = os.stat(path)
        items.append({"name": os.path.basename(path), "size": st.st_size,
                      "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc), "current": False})
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def resolve_log_file(name):
    """Absolute path for a log file name coming from the download route (always treated as
    untrusted — a URL segment), or None if it doesn't match the live file or a retained
    archive actually present on disk. Guards against path traversal."""
    if not isinstance(name, str) or "/" in name or "\\" in name:
        return None
    if name == os.path.basename(LOG_PATH) and os.path.isfile(LOG_PATH):
        return LOG_PATH
    if is_log_archive_name(name):
        path = os.path.join(DATA_DIR, name)
        return path if os.path.isfile(path) else None
    return None


# --- retention sweep ---------------------------------------------------------

_sweep_timer = None
_sweep_lock = threading.Lock()
SWEEP_INTERVAL_SECONDS = 24 * 60 * 60


def _sweep_tick():
    try:
        purge_expired_archives(storage.get_log_retention()["retention_days"])
    finally:
        _arm_sweep()


def _arm_sweep():
    global _sweep_timer
    with _sweep_lock:
        if _sweep_timer is not None:
            _sweep_timer.cancel()
        _sweep_timer = threading.Timer(SWEEP_INTERVAL_SECONDS, _sweep_tick)
        _sweep_timer.daemon = True
        _sweep_timer.start()


def start():
    """Called once from create_app(). Catches up retention immediately (in case the
    configured value was lowered while the app was stopped), then arms the daily sweep.
    With more than one gunicorn worker each process would run its own sweep (harmless here
    — purging is idempotent — but keep --workers 1 like backup/sync)."""
    purge_expired_archives(storage.get_log_retention()["retention_days"])
    _arm_sweep()


def _read_all():
    # Only the live file + the most recent archive: the live "Journal applicatif" / connection
    # history views stay bounded in size regardless of how much history retention keeps on
    # disk. Older archives are still there — reachable via list_log_files()/resolve_log_file()
    # for download — just not folded into this polled view.
    records = []
    latest = _latest_archive_path()
    for path in ([latest] if latest else []) + [LOG_PATH]:
        if os.path.exists(path):
            records.extend(_iter_records(path))
    records.sort(key=lambda r: r.get("seq", 0))
    return records


def _matches(rec, q):
    ql = q.lower()
    for key in ("message", "actor", "action", "category", "ip", "target", "path", "user_agent"):
        if ql in str(rec.get(key, "")).lower():
            return True
    return False


def tail(after_seq=None, q=None, categories=None, limit=500):
    """Return (records, last_seq). `records` oldest-first, filtered by category/substring,
    keeping only seq > after_seq, capped to the most recent `limit`."""
    records = _read_all()
    if categories:
        cats = set(categories)
        records = [r for r in records if r.get("category") in cats]
    if after_seq is not None:
        records = [r for r in records if r.get("seq", 0) > after_seq]
    if q:
        records = [r for r in records if _matches(r, q)]
    if limit and len(records) > limit:
        records = records[-limit:]
    return records, _seq


def connection_history(q=None, limit=200):
    """Auth events only, newest-first — feeds the connection-history table."""
    records, _ = tail(categories=["auth"], q=q, limit=limit)
    records.reverse()
    return records
