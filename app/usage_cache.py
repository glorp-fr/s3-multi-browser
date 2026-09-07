"""Cache of per-bucket volumetry (size/object count), refreshed at most every 24h.

Computing usage means listing every object in a bucket, which can be slow and is an
expensive operation to run against OOS on every page load — so results are cached on
disk and a manual refresh is rate-limited.
"""
import json
import os
import threading
from datetime import datetime, timedelta, timezone

DATA_DIR = (
    os.environ.get("MULTI_S3_BROWSER_DATA_DIR")
    or os.environ.get("OOS_VIEWER_DATA_DIR")  # ancien nom, gardé pour compat
    or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
)
CACHE_PATH = os.path.join(DATA_DIR, "usage_cache.json")

MIN_REFRESH_INTERVAL = timedelta(hours=24)

_lock = threading.Lock()


def _load():
    if not os.path.exists(CACHE_PATH):
        return {"buckets": {}}
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {"buckets": {}}
        return json.loads(content)


def _save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = CACHE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, CACHE_PATH)


def _key(account_id, bucket):
    return f"{account_id}/{bucket}"


def get(account_id, bucket):
    """Return {size_bytes, object_count, computed_at} or None if never computed."""
    entry = _load()["buckets"].get(_key(account_id, bucket))
    if not entry:
        return None
    return {
        "size_bytes": entry["size_bytes"],
        "object_count": entry["object_count"],
        "computed_at": datetime.fromisoformat(entry["computed_at"]),
    }


def next_refresh_at(entry):
    if not entry:
        return None
    return entry["computed_at"] + MIN_REFRESH_INTERVAL


def is_refresh_allowed(entry):
    if not entry:
        return True
    return datetime.now(timezone.utc) >= next_refresh_at(entry)


def set(account_id, bucket, size_bytes, object_count):
    with _lock:
        data = _load()
        data["buckets"][_key(account_id, bucket)] = {
            "size_bytes": size_bytes,
            "object_count": object_count,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        _save(data)


def compute_bucket_usage(client, bucket):
    """List every object in the bucket and sum sizes. Can be slow on large buckets."""
    size_bytes = 0
    object_count = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            size_bytes += obj["Size"]
            object_count += 1
    return size_bytes, object_count


def format_size(size_bytes):
    if size_bytes is None:
        return "—"
    tib = size_bytes / (1024 ** 4)
    if tib >= 1:
        return f"{tib:.2f} TiB"
    gib = size_bytes / (1024 ** 3)
    return f"{gib:.2f} GiB"
