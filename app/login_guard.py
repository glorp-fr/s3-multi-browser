"""In-memory brute-force guard for /login.

fail2ban itself doesn't fit this app's distribution model well — it's shipped as a
Docker image to external self-hosters (see README), and fail2ban needs host-level log
access and firewall privileges the container doesn't have and most self-hosters won't
bother wiring up. This reproduces its core behavior instead: N failed logins from one IP
within a window trigger a temporary ban of that IP, no matter which username was tried.

Single-process, in-memory — consistent with the "one gunicorn worker" requirement the
file-based storage already imposes elsewhere (usage_cache.py, backup.py).
"""
import os
import threading
from datetime import datetime, timedelta, timezone

MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
WINDOW = timedelta(minutes=int(os.environ.get("LOGIN_WINDOW_MINUTES", "5")))
BAN_DURATION = timedelta(minutes=int(os.environ.get("LOGIN_BAN_MINUTES", "15")))

_lock = threading.Lock()
_failures = {}       # ip -> [timestamps of recent failures, within WINDOW]
_banned_until = {}    # ip -> datetime the ban lifts


def _prune(ip, now):
    remaining = [t for t in _failures.get(ip, []) if now - t < WINDOW]
    if remaining:
        _failures[ip] = remaining
    else:
        _failures.pop(ip, None)


def is_banned(ip):
    """The ban-until datetime if `ip` is currently banned, else None."""
    if not ip:
        return None
    with _lock:
        until = _banned_until.get(ip)
        if until and until > datetime.now(timezone.utc):
            return until
        _banned_until.pop(ip, None)
        return None


def record_failure(ip):
    """Count one failed attempt from `ip`. Returns the new ban-until datetime if this
    attempt just crossed the threshold, else None."""
    if not ip:
        return None
    now = datetime.now(timezone.utc)
    with _lock:
        _prune(ip, now)
        _failures.setdefault(ip, []).append(now)
        if len(_failures[ip]) >= MAX_ATTEMPTS:
            until = now + BAN_DURATION
            _banned_until[ip] = until
            _failures.pop(ip, None)
            return until
        return None


def record_success(ip):
    """A successful login clears any failure count for that IP (but not an active ban —
    a banned IP stays banned even if it happens to guess right mid-ban)."""
    if not ip:
        return
    with _lock:
        _failures.pop(ip, None)
