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


def _empty_db():
    return {"users": [], "accounts": [], "providers": [], "_providers_seeded": False}


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
            "role": "admin",
            "account_ids": "*",
        })
        _save(data)


def migrate():
    """One-time, idempotent upgrade of the data file: seed default providers, and attach any
    pre-existing account (from before providers existed) to an "Outscale" provider so it keeps
    working unchanged."""
    with _lock:
        data = _load()
        changed = False

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


def create_user(username, password, role, account_ids):
    with _lock:
        data = _load()
        if any(u["username"] == username for u in data["users"]):
            raise ValueError("Ce nom d'utilisateur existe déjà")
        user = {
            "id": str(uuid.uuid4()),
            "username": username,
            "password_hash": generate_password_hash(password),
            "role": role,
            "account_ids": account_ids,
        }
        data["users"].append(user)
        _save(data)
        return user


def update_user(user_id, username, role, account_ids, password=None):
    with _lock:
        data = _load()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if not user:
            raise ValueError("Utilisateur introuvable")
        if any(u["username"] == username and u["id"] != user_id for u in data["users"]):
            raise ValueError("Ce nom d'utilisateur existe déjà")
        user["username"] = username
        user["role"] = role
        user["account_ids"] = account_ids
        if password:
            user["password_hash"] = generate_password_hash(password)
        _save(data)
        return user


def delete_user(user_id):
    with _lock:
        data = _load()
        data["users"] = [u for u in data["users"] if u["id"] != user_id]
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
        # Drop the account from any user's access list too.
        for user in data["users"]:
            if isinstance(user.get("account_ids"), list) and account_id in user["account_ids"]:
                user["account_ids"] = [a for a in user["account_ids"] if a != account_id]
        _save(data)
