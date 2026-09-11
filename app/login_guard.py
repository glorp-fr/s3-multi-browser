"""In-memory brute-force guard for /login (Administration > Sécurité).

fail2ban itself doesn't fit this app's distribution model well — it's shipped as a
Docker image to external self-hosters (see README), and fail2ban needs host-level log
access and firewall privileges the container doesn't have and most self-hosters won't
bother wiring up. This reproduces its core behavior instead: N failed logins from one IP
within a window trigger a temporary ban of that IP, no matter which username was tried.

Single-process, in-memory — consistent with the "one gunicorn worker" requirement the
file-based storage already imposes elsewhere (usage_cache.py, backup.py). Thresholds and
the on/off switch live in storage (`storage.get_login_protection()`), editable from
Administration > Sécurité without a restart — flipping "enabled" off takes effect on the
very next request, including for IPs already banned.
"""
import threading
from datetime import datetime, timedelta, timezone

from . import storage

_lock = threading.Lock()
_failures = {}       # ip -> [timestamps of recent failures, within the configured window]
_banned_until = {}    # ip -> datetime the ban lifts


def _prune(ip, now, window):
    remaining = [t for t in _failures.get(ip, []) if now - t < window]
    if remaining:
        _failures[ip] = remaining
    else:
        _failures.pop(ip, None)


def is_banned(ip):
    """The ban-until datetime if `ip` is currently banned, else None. Always None while
    the guard is disabled, even for an IP banned before it was turned off."""
    if not ip or not storage.get_login_protection()["enabled"]:
        return None
    with _lock:
        until = _banned_until.get(ip)
        if until and until > datetime.now(timezone.utc):
            return until
        _banned_until.pop(ip, None)
        return None


def record_failure(ip):
    """Count one failed attempt from `ip`. Returns the new ban-until datetime if this
    attempt just crossed the threshold, else None (also None, and not even counted, while
    the guard is disabled)."""
    cfg = storage.get_login_protection()
    if not ip or not cfg["enabled"]:
        return None
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=cfg["window_minutes"])
    with _lock:
        _prune(ip, now, window)
        _failures.setdefault(ip, []).append(now)
        if len(_failures[ip]) >= cfg["max_attempts"]:
            until = now + timedelta(minutes=cfg["ban_minutes"])
            _banned_until[ip] = until
            _failures.pop(ip, None)
            return until
        return None


def record_success(ip):
    """A successful login clears any failure count for that IP (but not an active ban —
    a banned IP stays banned even if it happens to guess right mid-ban; moot in practice
    since is_banned() is checked before credentials are, so this path isn't reachable for
    a currently-banned IP anyway)."""
    if not ip:
        return
    with _lock:
        _failures.pop(ip, None)
