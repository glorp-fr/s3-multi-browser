"""Session-based auth and role/account access helpers."""
from functools import wraps

from flask import abort, g, redirect, session, url_for

from . import storage

ROLES = ("admin", "operateur", "readonly")


def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = storage.get_user_by_id(user_id) if user_id else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("auth.login"))
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def can_write(user):
    return user["role"] in ("admin", "operateur")


def can_access_account(user, account_id):
    if user["role"] == "admin":
        return True
    ids = user.get("account_ids")
    return ids == "*" or account_id in (ids or [])


def accessible_accounts(user):
    accounts = storage.get_accounts()
    ids = user.get("account_ids")
    if user["role"] == "admin" or ids == "*":
        return accounts
    return [a for a in accounts if a["id"] in (ids or [])]
