"""Session-based auth and group/permission access helpers."""
from functools import wraps

from flask import abort, g, redirect, session, url_for

from . import storage

# Fine-grained capabilities carried by groups. `list`/navigation is implicit: any group
# that grants an account lets its members browse it.
PERMISSIONS = storage.GROUP_PERMISSIONS


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


def is_admin(user):
    return bool(user and user.get("is_admin"))


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        if not is_admin(g.user):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _user_groups(user):
    gids = set(user.get("group_ids") or [])
    if not gids:
        return []
    return [grp for grp in storage.get_groups() if grp["id"] in gids]


def _group_covers(group, account_id):
    return group.get("all_accounts") or account_id in (group.get("account_ids") or [])


def account_permissions(user, account_id):
    """Set of capabilities `user` holds on `account_id` (union across their groups).
    Admins hold every capability on every account."""
    if is_admin(user):
        return set(PERMISSIONS)
    caps = set()
    for group in _user_groups(user):
        if _group_covers(group, account_id):
            caps.update(p for p in (group.get("permissions") or []) if p in PERMISSIONS)
    return caps


def can(user, account_id, permission):
    return is_admin(user) or permission in account_permissions(user, account_id)


def can_access_account(user, account_id):
    if is_admin(user):
        return True
    return any(_group_covers(grp, account_id) for grp in _user_groups(user))


def accessible_accounts(user):
    accounts = storage.get_accounts()
    if is_admin(user):
        return accounts
    groups = _user_groups(user)
    if any(grp.get("all_accounts") for grp in groups):
        return accounts
    allowed = set()
    for grp in groups:
        allowed.update(grp.get("account_ids") or [])
    return [a for a in accounts if a["id"] in allowed]
