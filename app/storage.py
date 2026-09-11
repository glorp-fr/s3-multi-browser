"""JSON-file persistence for accounts and users. No database by design."""
import json
import os
import threading
import uuid
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
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


def _default_backup():
    """Config-backup settings (Administration > Sauvegarde). Secrets are stored encrypted
    in `*_enc` fields, exactly like S3 account Secret Keys."""
    return {
        "enabled": False,
        "destination": "s3",                 # "s3" | "smb"
        "frequency": "daily",                # "daily" | "weekly"
        "hour": 3, "minute": 0, "weekday": 0,  # weekday: 0=Mon .. 6=Sun, used when weekly
        "retention": 7,                      # keep the N most recent archives on the target
        "s3": {"endpoint": "", "region": "", "access_key": "",
               "secret_key_enc": "", "bucket": "", "prefix": "config-backups/"},
        "smb": {"server": "", "share": "", "path": "", "domain": "",
                "username": "", "password_enc": ""},
        "last_run": None, "last_status": None, "last_error": None, "last_archive": None,
    }


def _default_login_protection():
    """Brute-force guard on /login (Administration > Sécurité). Seeded from the
    LOGIN_* env vars (v0.9.6) on first run only, so upgrading doesn't silently change
    behavior for anyone who already set those — from then on this DB config is what's
    actually read; the env vars are ignored."""
    return {
        "enabled": True,
        "max_attempts": int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5")),
        "window_minutes": int(os.environ.get("LOGIN_WINDOW_MINUTES", "5")),
        "ban_minutes": int(os.environ.get("LOGIN_BAN_MINUTES", "15")),
    }


def _merge_defaults(target, defaults):
    """Recursively fill missing keys in `target` from `defaults` (in place)."""
    for key, val in defaults.items():
        if key not in target:
            target[key] = val
        elif isinstance(val, dict) and isinstance(target[key], dict):
            _merge_defaults(target[key], val)
    return target


def _empty_db():
    return {"users": [], "accounts": [], "providers": [], "groups": [],
            "backup": _default_backup(), "sync_jobs": [], "bucket_config_snapshots": [],
            "login_protection": _default_login_protection(), "_providers_seeded": False}


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
    data.setdefault("sync_jobs", [])
    data.setdefault("bucket_config_snapshots", [])
    data.setdefault("_providers_seeded", False)
    _merge_defaults(data.setdefault("backup", {}), _default_backup())
    _merge_defaults(data.setdefault("login_protection", {}), _default_login_protection())
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


# --- Backup config -------------------------------------------------------------

def get_backup_config():
    """Stored backup settings (secrets kept as opaque `*_enc` fields)."""
    return _load()["backup"]


def backup_smb_password(cfg=None):
    cfg = cfg or get_backup_config()
    enc = cfg["smb"].get("password_enc")
    return decrypt_secret(enc) if enc else ""


def backup_s3_secret_key(cfg=None):
    cfg = cfg or get_backup_config()
    enc = cfg["s3"].get("secret_key_enc")
    return decrypt_secret(enc) if enc else ""


def update_backup_config(*, enabled, destination, frequency, hour, minute, weekday,
                         retention, s3, smb):
    """`s3`/`smb` are dicts of plaintext fields; their `secret_key` / `password` entries are
    optional — left blank, the previously stored secret is kept."""
    if destination not in ("s3", "smb"):
        raise ValueError("Destination invalide")
    if frequency not in ("daily", "weekly"):
        raise ValueError("Fréquence invalide")
    hour, minute, weekday, retention = int(hour), int(minute), int(weekday), int(retention)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Horaire invalide")
    if not 0 <= weekday <= 6:
        raise ValueError("Jour de la semaine invalide")
    if retention < 1:
        raise ValueError("La rétention doit être d'au moins 1")

    with _lock:
        data = _load()
        b = data["backup"]
        b.update(enabled=bool(enabled), destination=destination, frequency=frequency,
                 hour=hour, minute=minute, weekday=weekday, retention=retention)

        b["s3"].update(
            endpoint=s3.get("endpoint", "").strip(),
            region=s3.get("region", "").strip(),
            access_key=s3.get("access_key", "").strip(),
            bucket=s3.get("bucket", "").strip(),
            prefix=s3.get("prefix", "").strip(),
        )
        if s3.get("secret_key", "").strip():
            b["s3"]["secret_key_enc"] = encrypt_secret(s3["secret_key"].strip())

        b["smb"].update(
            server=smb.get("server", "").strip(),
            share=smb.get("share", "").strip(),
            path=smb.get("path", "").strip(),
            domain=smb.get("domain", "").strip(),
            username=smb.get("username", "").strip(),
        )
        if smb.get("password", "").strip():
            b["smb"]["password_enc"] = encrypt_secret(smb["password"].strip())

        _save(data)
        return b


def record_backup_result(*, status, error=None, archive=None):
    with _lock:
        data = _load()
        data["backup"].update(
            last_run=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            last_status=status, last_error=error, last_archive=archive,
        )
        _save(data)
        return data["backup"]


def get_login_protection():
    """Brute-force guard settings (Administration > Sécurité)."""
    return _load()["login_protection"]


def update_login_protection(*, enabled, max_attempts, window_minutes, ban_minutes):
    max_attempts, window_minutes, ban_minutes = int(max_attempts), int(window_minutes), int(ban_minutes)
    if max_attempts < 1:
        raise ValueError("Le nombre d'essais avant bannissement doit être d'au moins 1")
    if window_minutes < 1 or ban_minutes < 1:
        raise ValueError("La fenêtre et la durée de bannissement doivent être d'au moins 1 minute")
    with _lock:
        data = _load()
        data["login_protection"] = {
            "enabled": bool(enabled), "max_attempts": max_attempts,
            "window_minutes": window_minutes, "ban_minutes": ban_minutes,
        }
        _save(data)
        return data["login_protection"]


# --- Sync jobs -------------------------------------------------------------
# A job copies (one-shot) or synchronizes (recurring) objects from one bucket to another,
# possibly on a different account/provider — no S3 CopyObject between endpoints, so the
# transfer streams through the app. See app/sync.py for the transfer engine + scheduler.

SYNC_SCOPES = ("object", "prefix", "bucket", "selection")  # "selection" = explicit key list
                                                             # (explorer bulk "Copier vers…")


def _default_sync_schedule():
    return {"enabled": False, "frequency": "daily", "weekday": 0, "hour": 3, "minute": 0}


def _default_sync_status():
    return {"state": "idle", "last_run_at": None, "last_summary": None}


def get_sync_jobs():
    return _load()["sync_jobs"]


def get_sync_jobs_by_owner(owner_id):
    return [j for j in get_sync_jobs() if j["owner_id"] == owner_id]


def get_sync_job_by_id(job_id):
    return next((j for j in get_sync_jobs() if j["id"] == job_id), None)


def _validate_sync_source(data, source):
    if not get_account_by_id_in(data, source.get("account_id")):
        raise ValueError("Compte source introuvable")
    if not source.get("bucket", "").strip():
        raise ValueError("Bucket source requis")
    scope = source.get("scope")
    if scope not in SYNC_SCOPES:
        raise ValueError("Portée source invalide")
    if scope in ("object", "prefix") and not (source.get("value") or "").strip():
        raise ValueError("Clé ou préfixe source requis pour cette portée")


def _validate_sync_dest(data, dest):
    if not get_account_by_id_in(data, dest.get("account_id")):
        raise ValueError("Compte destination introuvable")
    if not dest.get("bucket", "").strip():
        raise ValueError("Bucket destination requis")


def get_account_by_id_in(data, account_id):
    return next((a for a in data["accounts"] if a["id"] == account_id), None)


def _normalize_schedule(schedule):
    sched = dict(_default_sync_schedule())
    sched["enabled"] = bool((schedule or {}).get("enabled"))
    freq = (schedule or {}).get("frequency", "daily")
    if freq not in ("daily", "weekly"):
        raise ValueError("Fréquence invalide")
    sched["frequency"] = freq
    try:
        sched["weekday"] = int((schedule or {}).get("weekday", 0))
        sched["hour"] = int((schedule or {}).get("hour", 3))
        sched["minute"] = int((schedule or {}).get("minute", 0))
    except (TypeError, ValueError):
        raise ValueError("Planification invalide")
    if not (0 <= sched["hour"] <= 23 and 0 <= sched["minute"] <= 59):
        raise ValueError("Horaire invalide")
    if not 0 <= sched["weekday"] <= 6:
        raise ValueError("Jour de la semaine invalide")
    return sched


def _clean_sync_source(source):
    return {
        "account_id": source["account_id"],
        "bucket": source["bucket"].strip(),
        "scope": source["scope"],
        "value": (source.get("value") or "").strip() if source["scope"] != "bucket" else "",
        "keys": list(source.get("keys") or []) if source["scope"] == "selection" else [],
    }


def _clean_sync_dest(dest):
    prefix = (dest.get("prefix") or "").lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return {"account_id": dest["account_id"], "bucket": dest["bucket"].strip(), "prefix": prefix}


def create_sync_job(*, owner_id, name, source, dest, delete_extraneous, schedule):
    name = (name or "").strip() or "Sans nom"
    with _lock:
        data = _load()
        _validate_sync_source(data, source)
        _validate_sync_dest(data, dest)
        job = {
            "id": str(uuid.uuid4()),
            "owner_id": owner_id,
            "name": name,
            "source": _clean_sync_source(source),
            "dest": _clean_sync_dest(dest),
            "delete_extraneous": bool(delete_extraneous),
            "schedule": _normalize_schedule(schedule),
            "enabled": True,
            "status": _default_sync_status(),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        data["sync_jobs"].append(job)
        _save(data)
        return job


def update_sync_job(job_id, *, name, source, dest, delete_extraneous, schedule):
    with _lock:
        data = _load()
        job = next((j for j in data["sync_jobs"] if j["id"] == job_id), None)
        if not job:
            raise ValueError("Job de synchronisation introuvable")
        _validate_sync_source(data, source)
        _validate_sync_dest(data, dest)
        job["name"] = (name or "").strip() or "Sans nom"
        job["source"] = _clean_sync_source(source)
        job["dest"] = _clean_sync_dest(dest)
        job["delete_extraneous"] = bool(delete_extraneous)
        job["schedule"] = _normalize_schedule(schedule)
        # A job edited by its owner is trusted again (clears an earlier auto-disable).
        job["enabled"] = True
        if job["status"]["state"] == "rights_error":
            job["status"]["state"] = "idle"
        _save(data)
        return job


def delete_sync_job(job_id):
    with _lock:
        data = _load()
        data["sync_jobs"] = [j for j in data["sync_jobs"] if j["id"] != job_id]
        _save(data)


def set_sync_job_enabled(job_id, enabled, *, state=None):
    with _lock:
        data = _load()
        job = next((j for j in data["sync_jobs"] if j["id"] == job_id), None)
        if not job:
            raise ValueError("Job de synchronisation introuvable")
        job["enabled"] = bool(enabled)
        if state:
            job["status"]["state"] = state
        _save(data)
        return job


def reassign_sync_job(job_id, new_owner_id):
    """Admin takeover: re-point a job at a new owner and re-enable it (used when the
    original owner lost the rights the job needs)."""
    with _lock:
        data = _load()
        job = next((j for j in data["sync_jobs"] if j["id"] == job_id), None)
        if not job:
            raise ValueError("Job de synchronisation introuvable")
        job["owner_id"] = new_owner_id
        job["enabled"] = True
        job["status"]["state"] = "idle"
        _save(data)
        return job


def record_sync_job_result(job_id, *, state, summary):
    with _lock:
        data = _load()
        job = next((j for j in data["sync_jobs"] if j["id"] == job_id), None)
        if not job:
            return None
        job["status"]["state"] = state
        job["status"]["last_run_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        job["status"]["last_summary"] = summary
        _save(data)
        return job


# --- Bucket config undo snapshots -----------------------------------------------------
# One-level undo: each (account_id, bucket, section) key holds the state captured right
# before the last "Enregistrer" on that section. Overwritten on every new apply; cleared
# once "Annuler" replays it, so a second undo has nothing left to restore.

def _bucket_config_key(account_id, bucket, section):
    return f"{account_id}/{bucket}/{section}"


def get_bucket_config_snapshot(account_id, bucket, section):
    key = _bucket_config_key(account_id, bucket, section)
    return next((s for s in _load()["bucket_config_snapshots"] if s["id"] == key), None)


def save_bucket_config_snapshot(account_id, bucket, section, value, saved_by):
    key = _bucket_config_key(account_id, bucket, section)
    with _lock:
        data = _load()
        data["bucket_config_snapshots"] = [
            s for s in data["bucket_config_snapshots"] if s["id"] != key
        ]
        data["bucket_config_snapshots"].append({
            "id": key,
            "account_id": account_id,
            "bucket": bucket,
            "section": section,
            "value": value,
            "saved_by": saved_by,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        _save(data)


def delete_bucket_config_snapshot(account_id, bucket, section):
    key = _bucket_config_key(account_id, bucket, section)
    with _lock:
        data = _load()
        data["bucket_config_snapshots"] = [
            s for s in data["bucket_config_snapshots"] if s["id"] != key
        ]
        _save(data)
