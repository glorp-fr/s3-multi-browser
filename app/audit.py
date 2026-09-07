"""Append-only audit / activity log. JSON Lines on disk — no database, same spirit
as storage.py / usage_cache.py (lock + plain files).

One line per event. `seq` is a strictly increasing integer used as a cursor by the
live log view (poll `?after=<seq>`). The file is capped and rotated once
(`audit.jsonl` -> `audit.jsonl.1`) so it can never grow unbounded.
"""
import json
import os
import threading
from datetime import datetime, timezone

from flask import g, has_request_context, request

from .storage import DATA_DIR

LOG_PATH = os.path.join(DATA_DIR, "audit.jsonl")
ROTATED_PATH = LOG_PATH + ".1"
MAX_BYTES = 5 * 1024 * 1024

# Categories used across the app. Kept in sync with the filter checkboxes in admin_logs.html.
CATEGORIES = ("auth", "s3_read", "s3_write", "admin")

_lock = threading.Lock()
_seq = 0


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
    global _seq
    last = 0
    for path in (ROTATED_PATH, LOG_PATH):
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
                os.replace(LOG_PATH, ROTATED_PATH)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return rec


def _read_all():
    records = []
    for path in (ROTATED_PATH, LOG_PATH):
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
