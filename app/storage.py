"""JSON-file persistence for accounts and users. No database by design."""
import json
import os
import threading
import uuid
from base64 import urlsafe_b64encode
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken
from werkzeug.security import check_password_hash, generate_password_hash

DATA_DIR = (
    os.environ.get("MULTI_S3_BROWSER_DATA_DIR")
    or os.environ.get("OOS_VIEWER_DATA_DIR")  # ancien nom, gardé pour compat
    or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
)
DB_PATH = os.path.join(DATA_DIR, "db.json")

_lock = threading.Lock()

# Seed data for the two built-in providers created on first run. Fully editable/deletable
# afterwards from Administration > Providers — these are just a convenient starting point.
DEFAULT_PROVIDERS = [
    {
        "name": "Outscale",
        "endpoint_template": "https://oos.{region}.outscale.com",
        "regions": ["eu-west-2", "us-east-2", "us-west-1", "ap-northeast-1", "cloudgouv-eu-west-1"],
    },
    {
        "name": "AWS",
        "endpoint_template": "https://s3.{region}.amazonaws.com",
        "regions": [
            "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1",
            "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
            "ap-northeast-1", "ap-northeast-2", "ap-southeast-1", "ap-southeast-2",
            "ap-south-1", "sa-east-1",
        ],
    },
]


def _fernet():
    master_key = os.environ.get("APP_MASTER_KEY")
    if not master_key:
        raise RuntimeError("APP_MASTER_KEY environment variable is required to encrypt/decrypt account secrets")
    # Derive a valid 32-byte urlsafe-base64 Fernet key from whatever string the operator provides.
    derived = urlsafe_b64encode(sha256(master_key.encode("utf-8")).digest())
    return Fernet(derived)


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt stored secret key (wrong APP_MASTER_KEY?)") from exc


# Fine-grained capabilities a group can grant on the accounts it covers. `list`/navigation
# is implicit: any group that grants an account lets its members browse it.
GROUP_PERMISSIONS = ("download", "upload", "delete", "bucket_admin")


def _empty_db():
    return {"users": [], "accounts": [], "providers": [], "groups": [], "_providers_seeded": False}


def _load():
    if not os.path.exists(DB_PATH):
        return _empty_db()
    with open(DB_PATH, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            data = _empty_db()
        else:
            data = json.loads(content)
    data.setdefault("providers", [])
    data.setdefault("groups", [])
    data.setdefault("_providers_seeded", False)
    return data


def _save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = DB_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, DB_PATH)


def bootstrap_admin_if_empty():
    """Create the first admin user from env vars if no users exist yet."""
    with _lock:
        data = _load()
        if data["users"]:
            return
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        if not username or not password:
            return
        data["users"].append({
            "id": str(uuid.uuid4()),
            "username": username,
            "password_hash": generate_password_hash(password),
            "is_admin": True,
            "group_ids": [],
        })
        _save(data)


def migrate():
    """One-time, idempotent upgrade of the data file: seed default providers, attach any
    pre-existing account (from before providers existed) to an "Outscale" provider so it keeps
    working unchanged, and move users off the old global-role model onto groups."""
    with _lock:
        data = _load()
        changed = False

        # Old per-user model: role in {admin, operateur, readonly} + account_ids ("*" | [ids]).
        # New model: is_admin flag + group_ids. Non-admin access is not auto-migrated (decided
        # with the operator) — the admin re-grants access through groups.
        for user in data["users"]:
            if "role" in user:
                user["is_admin"] = user.pop("role") == "admin"
                changed = True
            if "account_ids" in user:
                del user["account_ids"]
                changed = True
            if "group_ids" not in user:
                user["group_ids"] = []
                changed = True

        if not data["_providers_seeded"]:
            if not data["providers"]:
                for preset in DEFAULT_PROVIDERS:
                    data["providers"].append({"id": str(uuid.uuid4()), **preset})
            data["_providers_seeded"] = True
            changed = True

        legacy_accounts = [a for a in data["accounts"] if "provider_id" not in a]
        if legacy_accounts:
            outscale = next((p for p in data["providers"] if p["name"] == "Outscale"), None)
            if outscale is None:
                outscale = {
                    "id": str(uuid.uuid4()),
                    "name": "Outscale",
                    "endpoint_template": "https://oos.{region}.outscale.com",
                    "regions": [],
                }
                data["providers"].append(outscale)
            for account in legacy_accounts:
                account["provider_id"] = outscale["id"]
                if account.get("region") and account["region"] not in outscale["regions"]:
                    outscale["regions"].append(account["region"])
            changed = True

        if changed:
            _save(data)


# --- Users ---------------------------------------------------------------

def get_users():
    return _load()["users"]


def get_user_by_id(user_id):
    return next((u for u in get_users() if u["id"] == user_id), None)


def get_user_by_username(username):
    return next((u for u in get_users() if u["username"] == username), None)


def verify_login(username, password):
    user = get_user_by_username(username)
    if not user:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


def _sanitize_group_ids(data, group_ids):
    known = {g["id"] for g in data["groups"]}
    seen = []
    for gid in group_ids or []:
        if gid in known and gid not in seen:
            seen.append(gid)
    return seen


def _last_admin_guard(data, user_id, still_admin):
    """Raise if applying `still_admin` to `user_id` would leave zero administrators."""
    admins = {u["id"] for u in data["users"] if u.get("is_admin")}
    if not still_admin:
        admins.discard(user_id)
    if not admins:
        raise ValueError("Impossible : il doit rester au moins un administrateur")


def create_user(username, password, is_admin, group_ids):
    with _lock:
        data = _load()
        if any(u["username"] == username for u in data["users"]):
            raise ValueError("Ce nom d'utilisateur existe déjà")
        user = {
            "id": str(uuid.uuid4()),
            "username": username,
            "password_hash": generate_password_hash(password),
            "is_admin": bool(is_admin),
            "group_ids": [] if is_admin else _sanitize_group_ids(data, group_ids),
        }
        data["users"].append(user)
        _save(data)
        return user


def update_user(user_id, username, is_admin, group_ids, password=None):
    with _lock:
        data = _load()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if not user:
            raise ValueError("Utilisateur introuvable")
        if any(u["username"] == username and u["id"] != user_id for u in data["users"]):
            raise ValueError("Ce nom d'utilisateur existe déjà")
        _last_admin_guard(data, user_id, bool(is_admin))
        user["username"] = username
        user["is_admin"] = bool(is_admin)
        user["group_ids"] = [] if is_admin else _sanitize_group_ids(data, group_ids)
        if password:
            user["password_hash"] = generate_password_hash(password)
        _save(data)
        return user


def delete_user(user_id):
    with _lock:
        data = _load()
        if not any(u["id"] == user_id for u in data["users"]):
            return
        _last_admin_guard(data, user_id, still_admin=False)
        data["users"] = [u for u in data["users"] if u["id"] != user_id]
        _save(data)


# --- Groups ----------------------------------------------------------------

def get_groups():
    return _load()["groups"]


def get_group_by_id(group_id):
    return next((g for g in get_groups() if g["id"] == group_id), None)


def _clean_permissions(permissions):
    return [p for p in GROUP_PERMISSIONS if p in (permissions or [])]


def _clean_account_ids(data, account_ids):
    known = {a["id"] for a in data["accounts"]}
    seen = []
    for aid in account_ids or []:
        if aid in known and aid not in seen:
            seen.append(aid)
    return seen


def create_group(name, permissions, all_accounts, account_ids):
    name = (name or "").strip()
    if not name:
        raise ValueError("Nom de groupe requis")
    with _lock:
        data = _load()
        if any(g["name"] == name for g in data["groups"]):
            raise ValueError("Ce groupe existe déjà")
        group = {
            "id": str(uuid.uuid4()),
            "name": name,
            "permissions": _clean_permissions(permissions),
            "all_accounts": bool(all_accounts),
            "account_ids": [] if all_accounts else _clean_account_ids(data, account_ids),
        }
        data["groups"].append(group)
        _save(data)
        return group


def update_group(group_id, name, permissions, all_accounts, account_ids):
    name = (name or "").strip()
    if not name:
        raise ValueError("Nom de groupe requis")
    with _lock:
        data = _load()
        group = next((g for g in data["groups"] if g["id"] == group_id), None)
        if not group:
            raise ValueError("Groupe introuvable")
        if any(g["name"] == name and g["id"] != group_id for g in data["groups"]):
            raise ValueError("Ce groupe existe déjà")
        group["name"] = name
        group["permissions"] = _clean_permissions(permissions)
        group["all_accounts"] = bool(all_accounts)
        group["account_ids"] = [] if all_accounts else _clean_account_ids(data, account_ids)
        _save(data)
        return group


def delete_group(group_id):
    with _lock:
        data = _load()
        members = [u["username"] for u in data["users"] if group_id in (u.get("group_ids") or [])]
        if members:
            raise ValueError(
                "Impossible de supprimer : des utilisateurs sont encore dans ce groupe ("
                + ", ".join(members) + ")"
            )
        data["groups"] = [g for g in data["groups"] if g["id"] != group_id]
        _save(data)


# --- Providers ---------------------------------------------------------------

def get_providers():
    return _load()["providers"]


def get_provider_by_id(provider_id):
    return next((p for p in get_providers() if p["id"] == provider_id), None)


def _parse_regions(raw_regions):
    """raw_regions: iterable of strings (e.g. textarea lines) -> deduped, ordered list."""
    seen = []
    for r in raw_regions:
        r = r.strip()
        if r and r not in seen:
            seen.append(r)
    return seen


def create_provider(name, endpoint_template, raw_regions):
    if "{region}" not in endpoint_template:
        raise ValueError("Le endpoint doit contenir le paramètre {region}")
    with _lock:
        data = _load()
        if any(p["name"] == name for p in data["providers"]):
            raise ValueError("Ce provider existe déjà")
        provider = {
            "id": str(uuid.uuid4()),
            "name": name,
            "endpoint_template": endpoint_template,
            "regions": _parse_regions(raw_regions),
        }
        data["providers"].append(provider)
        _save(data)
        return provider


def update_provider(provider_id, name, endpoint_template, raw_regions):
    if "{region}" not in endpoint_template:
        raise ValueError("Le endpoint doit contenir le paramètre {region}")
    with _lock:
        data = _load()
        provider = next((p for p in data["providers"] if p["id"] == provider_id), None)
        if not provider:
            raise ValueError("Provider introuvable")
        if any(p["name"] == name and p["id"] != provider_id for p in data["providers"]):
            raise ValueError("Ce provider existe déjà")
        provider["name"] = name
        provider["endpoint_template"] = endpoint_template
        provider["regions"] = _parse_regions(raw_regions)
        _save(data)
        return provider


def delete_provider(provider_id):
    with _lock:
        data = _load()
        if any(a["provider_id"] == provider_id for a in data["accounts"]):
            raise ValueError("Impossible de supprimer : des comptes utilisent encore ce provider")
        data["providers"] = [p for p in data["providers"] if p["id"] != provider_id]
        _save(data)


# --- Accounts --------------------------------------------------------------

def get_accounts():
    return _load()["accounts"]


def get_account_by_id(account_id):
    return next((a for a in get_accounts() if a["id"] == account_id), None)


def _validate_provider_region(data, provider_id, region):
    provider = next((p for p in data["providers"] if p["id"] == provider_id), None)
    if not provider:
        raise ValueError("Provider introuvable")
    if region not in provider["regions"]:
        raise ValueError("Région invalide pour ce provider")
    return provider


def create_account(name, account_number, provider_id, region, access_key, secret_key):
    with _lock:
        data = _load()
        _validate_provider_region(data, provider_id, region)
        account = {
            "id": str(uuid.uuid4()),
            "name": name,
            "account_number": account_number,
            "provider_id": provider_id,
            "region": region,
            "access_key": access_key,
            "secret_key_enc": encrypt_secret(secret_key),
        }
        data["accounts"].append(account)
        _save(data)
        return account


def update_account(account_id, name, account_number, provider_id, region, access_key, secret_key=None):
    with _lock:
        data = _load()
        account = next((a for a in data["accounts"] if a["id"] == account_id), None)
        if not account:
            raise ValueError("Compte introuvable")
        _validate_provider_region(data, provider_id, region)
        account["name"] = name
        account["account_number"] = account_number
        account["provider_id"] = provider_id
        account["region"] = region
        account["access_key"] = access_key
        if secret_key:
            account["secret_key_enc"] = encrypt_secret(secret_key)
        _save(data)
        return account


def delete_account(account_id):
    with _lock:
        data = _load()
        data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
        # Drop the account from any group that referenced it explicitly.
        for group in data["groups"]:
            if account_id in (group.get("account_ids") or []):
                group["account_ids"] = [a for a in group["account_ids"] if a != account_id]
        _save(data)
