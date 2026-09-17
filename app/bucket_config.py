"""Read/write helpers for bucket-level S3 settings: versioning, object lock default
retention, lifecycle rules, bucket policy and ACL. Each `get_*` returns a plain-dict/str/list
snapshot that can be fed straight back into the matching `set_*`/`restore_*` — this symmetry
is what lets the routes implement a generic one-level undo (capture `get_*` before calling
`set_*`, replay it on undo)."""
import json
import uuid

from botocore.exceptions import ClientError


def _not_found(exc, *codes):
    return exc.response.get("Error", {}).get("Code") in codes


# --- Versioning --------------------------------------------------------------------------

def get_versioning(client, bucket):
    resp = client.get_bucket_versioning(Bucket=bucket)
    return resp.get("Status")  # "Enabled" | "Suspended" | None (never configured)


def set_versioning(client, bucket, status):
    if status not in ("Enabled", "Suspended"):
        raise ValueError("Statut de versionning invalide")
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": status})


# --- Object Lock (default retention only — ObjectLockEnabled is immutable after creation) --

def get_object_lock(client, bucket):
    try:
        resp = client.get_object_lock_configuration(Bucket=bucket)
    except ClientError as exc:
        if _not_found(exc, "ObjectLockConfigurationNotFoundError"):
            return {"supported": True, "enabled": False, "rule": None}
        if _not_found(exc, "NotImplemented", "MethodNotAllowed", "UnsupportedOperation"):
            return {"supported": False, "enabled": False, "rule": None}
        raise
    cfg = resp.get("ObjectLockConfiguration", {})
    enabled = cfg.get("ObjectLockEnabled") == "Enabled"
    retention = (cfg.get("Rule") or {}).get("DefaultRetention")
    rule = None
    if retention:
        rule = {
            "mode": retention.get("Mode"),
            "days": retention.get("Days"),
            "years": retention.get("Years"),
        }
    return {"supported": True, "enabled": enabled, "rule": rule}


def set_object_lock_rule(client, bucket, rule):
    """Update (or clear) the default retention rule. `rule` is None to clear, or
    {"mode": "GOVERNANCE"|"COMPLIANCE", "days": int} / {"mode": ..., "years": int}."""
    cfg = {"ObjectLockEnabled": "Enabled"}
    if rule:
        retention = {"Mode": rule["mode"]}
        if rule.get("days"):
            retention["Days"] = int(rule["days"])
        elif rule.get("years"):
            retention["Years"] = int(rule["years"])
        cfg["Rule"] = {"DefaultRetention": retention}
    client.put_object_lock_configuration(Bucket=bucket, ObjectLockConfiguration=cfg)


# --- Encryption (SSE-S3 / AES256 only — see set_encryption) ------------------------------

def get_encryption(client, bucket):
    try:
        resp = client.get_bucket_encryption(Bucket=bucket)
    except ClientError as exc:
        if _not_found(exc, "ServerSideEncryptionConfigurationNotFoundError"):
            return {"supported": True, "enabled": False}
        if _not_found(exc, "NotImplemented", "MethodNotAllowed", "UnsupportedOperation"):
            return {"supported": False, "enabled": False}
        raise
    rules = resp.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
    enabled = any(r.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
                 for r in rules)
    return {"supported": True, "enabled": enabled}


def set_encryption(client, bucket, enabled):
    """Only SSE-S3 (AES256, provider-managed key) — no KMS key to configure."""
    if enabled:
        client.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
    else:
        try:
            client.delete_bucket_encryption(Bucket=bucket)
        except ClientError as exc:
            if not _not_found(exc, "ServerSideEncryptionConfigurationNotFoundError"):
                raise


# --- Lifecycle ---------------------------------------------------------------------------

def get_lifecycle(client, bucket):
    try:
        resp = client.get_bucket_lifecycle_configuration(Bucket=bucket)
    except ClientError as exc:
        if _not_found(exc, "NoSuchLifecycleConfiguration"):
            return []
        raise
    rules = []
    for r in resp.get("Rules", []):
        filt = r.get("Filter") or {}
        prefix = filt.get("Prefix")
        if prefix is None and "Prefix" in r:  # older-style rules without Filter wrapper
            prefix = r.get("Prefix")
        rules.append({
            "id": r.get("ID") or "",
            "prefix": prefix or "",
            "enabled": r.get("Status") == "Enabled",
            "expiration_days": (r.get("Expiration") or {}).get("Days"),
            "noncurrent_expiration_days":
                (r.get("NoncurrentVersionExpiration") or {}).get("NoncurrentDays"),
            "abort_incomplete_multipart_days":
                (r.get("AbortIncompleteMultipartUpload") or {}).get("DaysAfterInitiation"),
        })
    return rules


def set_lifecycle(client, bucket, rules):
    if not rules:
        try:
            client.delete_bucket_lifecycle(Bucket=bucket)
        except ClientError as exc:
            if not _not_found(exc, "NoSuchLifecycleConfiguration"):
                raise
        return
    aws_rules = []
    for r in rules:
        rule = {
            "ID": r.get("id") or str(uuid.uuid4()),
            "Filter": {"Prefix": r.get("prefix") or ""},
            "Status": "Enabled" if r.get("enabled") else "Disabled",
        }
        if r.get("expiration_days"):
            rule["Expiration"] = {"Days": int(r["expiration_days"])}
        if r.get("noncurrent_expiration_days"):
            rule["NoncurrentVersionExpiration"] = {"NoncurrentDays": int(r["noncurrent_expiration_days"])}
        if r.get("abort_incomplete_multipart_days"):
            rule["AbortIncompleteMultipartUpload"] = {
                "DaysAfterInitiation": int(r["abort_incomplete_multipart_days"])
            }
        if "Expiration" not in rule and "NoncurrentVersionExpiration" not in rule \
                and "AbortIncompleteMultipartUpload" not in rule:
            # A rule with no action at all is rejected by S3 — skip it rather than fail the batch.
            continue
        aws_rules.append(rule)
    client.put_bucket_lifecycle_configuration(
        Bucket=bucket, LifecycleConfiguration={"Rules": aws_rules}
    )


# --- Bucket policy -------------------------------------------------------------------------

def get_policy(client, bucket):
    try:
        resp = client.get_bucket_policy(Bucket=bucket)
    except ClientError as exc:
        if _not_found(exc, "NoSuchBucketPolicy"):
            return None
        raise
    return resp.get("Policy")


def set_policy(client, bucket, policy_json):
    if not policy_json or not policy_json.strip():
        try:
            client.delete_bucket_policy(Bucket=bucket)
        except ClientError as exc:
            if not _not_found(exc, "NoSuchBucketPolicy"):
                raise
        return
    parsed = json.loads(policy_json)  # raises ValueError on invalid JSON — caller flashes it
    client.put_bucket_policy(Bucket=bucket, Policy=json.dumps(parsed))


# --- ACL -------------------------------------------------------------------------------

CANNED_ACLS = ("private", "public-read", "public-read-write", "authenticated-read")
# "bucket-owner-read" / "bucket-owner-full-control" are only valid for PutObjectAcl /
# CopyObject, not PutBucketAcl (rejected as InvalidArgument if attempted on a bucket) —
# deliberately excluded here.
PUBLIC_ACLS = ("public-read", "public-read-write")


def get_acl(client, bucket):
    resp = client.get_bucket_acl(Bucket=bucket)
    owner = resp.get("Owner", {})
    grants = []
    for g in resp.get("Grants", []):
        grantee = g.get("Grantee", {})
        grants.append({
            "type": grantee.get("Type"),
            "id": grantee.get("ID"),
            "uri": grantee.get("URI"),
            "display_name": grantee.get("DisplayName"),
            "permission": g.get("Permission"),
        })
    return {"owner": owner, "grants": grants}


_ALL_USERS_URI = "http://acs.amazonaws.com/groups/global/AllUsers"
_AUTH_USERS_URI = "http://acs.amazonaws.com/groups/global/AuthenticatedUsers"


def detect_canned_acl(snapshot):
    """Best-effort match of a get_acl() snapshot against one of the four bucket-level canned
    ACLs — None if the current grants are custom (e.g. explicit cross-account grants) and
    not representable as one of them."""
    owner_id = (snapshot.get("owner") or {}).get("ID")
    non_owner = {
        (g.get("uri"), g["permission"]) for g in snapshot.get("grants", [])
        if not (g.get("type") == "CanonicalUser" and g.get("id") == owner_id
                and g["permission"] == "FULL_CONTROL")
    }
    if not non_owner:
        return "private"
    if non_owner == {(_ALL_USERS_URI, "READ")}:
        return "public-read"
    if non_owner == {(_ALL_USERS_URI, "READ"), (_ALL_USERS_URI, "WRITE")}:
        return "public-read-write"
    if non_owner == {(_AUTH_USERS_URI, "READ")}:
        return "authenticated-read"
    return None


GRANT_PERMISSIONS = ("READ", "WRITE", "READ_ACP", "WRITE_ACP", "FULL_CONTROL")

_CANNED_EXTRA_GRANTS = {
    "private": [],
    "public-read": [{"uri": _ALL_USERS_URI, "permission": "READ"}],
    "public-read-write": [
        {"uri": _ALL_USERS_URI, "permission": "READ"},
        {"uri": _ALL_USERS_URI, "permission": "WRITE"},
    ],
    "authenticated-read": [{"uri": _AUTH_USERS_URI, "permission": "READ"}],
}


def apply_acl(client, bucket, owner, canned, account_grants):
    """Apply an ACL built from an optional canned preset plus explicit per-account grants,
    in a single put_bucket_acl(AccessControlPolicy=...) call — the only way to combine both,
    since `ACL=<canned>` on its own replaces the whole grant list. `owner` is the raw Owner
    dict from get_acl(); `account_grants` is a list of {"identifier_type": "id"|"email",
    "identifier": str, "permission": str}."""
    if canned and canned not in CANNED_ACLS:
        raise ValueError("ACL prédéfinie invalide")
    grants = [{"Grantee": {"Type": "CanonicalUser", "ID": owner["ID"]}, "Permission": "FULL_CONTROL"}]
    for extra in _CANNED_EXTRA_GRANTS.get(canned, []):
        grants.append({"Grantee": {"Type": "Group", "URI": extra["uri"]}, "Permission": extra["permission"]})
    for g in account_grants:
        if g["permission"] not in GRANT_PERMISSIONS:
            raise ValueError("Permission invalide")
        identifier = g["identifier"].strip()
        if not identifier:
            continue
        if g["identifier_type"] == "email":
            grantee = {"Type": "AmazonCustomerByEmail", "EmailAddress": identifier}
        else:
            grantee = {"Type": "CanonicalUser", "ID": identifier}
        grants.append({"Grantee": grantee, "Permission": g["permission"]})
    client.put_bucket_acl(Bucket=bucket, AccessControlPolicy={"Owner": owner, "Grants": grants})


def restore_acl(client, bucket, snapshot):
    """Exact restore of a previous get_acl() snapshot (owner + explicit grants), regardless of
    whether it originally came from a canned ACL or custom grants."""
    grants = []
    for g in snapshot.get("grants", []):
        grantee = {"Type": g["type"]}
        if g.get("id"):
            grantee["ID"] = g["id"]
        if g.get("uri"):
            grantee["URI"] = g["uri"]
        if g.get("display_name"):
            grantee["DisplayName"] = g["display_name"]
        grants.append({"Grantee": grantee, "Permission": g["permission"]})
    client.put_bucket_acl(
        Bucket=bucket,
        AccessControlPolicy={"Owner": snapshot.get("owner", {}), "Grants": grants},
    )
