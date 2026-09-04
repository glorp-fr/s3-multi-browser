from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import storage
from ..auth import ROLES, role_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


# --- Accounts ---------------------------------------------------------------

@bp.route("/accounts")
@role_required("admin")
def accounts():
    return render_template("admin_accounts.html", accounts=storage.get_accounts())


@bp.route("/accounts/new", methods=["GET", "POST"])
@role_required("admin")
def account_new():
    if request.method == "POST":
        try:
            storage.create_account(
                name=request.form["name"].strip(),
                account_number=request.form.get("account_number", "").strip(),
                region=request.form["region"].strip(),
                access_key=request.form["access_key"].strip(),
                secret_key=request.form["secret_key"].strip(),
            )
            flash("Compte créé", "success")
            return redirect(url_for("admin.accounts"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_account_form.html", account=None)


@bp.route("/accounts/<account_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def account_edit(account_id):
    account = storage.get_account_by_id(account_id)
    if not account:
        flash("Compte introuvable", "error")
        return redirect(url_for("admin.accounts"))
    if request.method == "POST":
        try:
            storage.update_account(
                account_id,
                name=request.form["name"].strip(),
                account_number=request.form.get("account_number", "").strip(),
                region=request.form["region"].strip(),
                access_key=request.form["access_key"].strip(),
                secret_key=request.form.get("secret_key", "").strip() or None,
            )
            flash("Compte mis à jour", "success")
            return redirect(url_for("admin.accounts"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_account_form.html", account=account)


@bp.route("/accounts/<account_id>/delete", methods=["POST"])
@role_required("admin")
def account_delete(account_id):
    storage.delete_account(account_id)
    flash("Compte supprimé", "success")
    return redirect(url_for("admin.accounts"))


# --- Users -------------------------------------------------------------------

@bp.route("/users")
@role_required("admin")
def users():
    return render_template("admin_users.html", users=storage.get_users(), accounts=storage.get_accounts())


def _account_ids_from_form():
    if request.form.get("all_accounts"):
        return "*"
    return request.form.getlist("account_ids")


@bp.route("/users/new", methods=["GET", "POST"])
@role_required("admin")
def user_new():
    if request.method == "POST":
        try:
            storage.create_user(
                username=request.form["username"].strip(),
                password=request.form["password"],
                role=request.form["role"],
                account_ids=_account_ids_from_form(),
            )
            flash("Utilisateur créé", "success")
            return redirect(url_for("admin.users"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_user_form.html", user=None, roles=ROLES, accounts=storage.get_accounts())


@bp.route("/users/<user_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def user_edit(user_id):
    user = storage.get_user_by_id(user_id)
    if not user:
        flash("Utilisateur introuvable", "error")
        return redirect(url_for("admin.users"))
    if request.method == "POST":
        try:
            storage.update_user(
                user_id,
                username=request.form["username"].strip(),
                role=request.form["role"],
                account_ids=_account_ids_from_form(),
                password=request.form.get("password") or None,
            )
            flash("Utilisateur mis à jour", "success")
            return redirect(url_for("admin.users"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_user_form.html", user=user, roles=ROLES, accounts=storage.get_accounts())


@bp.route("/users/<user_id>/delete", methods=["POST"])
@role_required("admin")
def user_delete(user_id):
    storage.delete_user(user_id)
    flash("Utilisateur supprimé", "success")
    return redirect(url_for("admin.users"))
