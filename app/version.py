"""Version display + self-update against GitHub.

Deux modes de déploiement, détectés au runtime :

- **git checkout** (gunicorn sur l'hôte) : "check" = compare GitHub de HEAD contre la tête
  de la branche par défaut ; "update" = `git fetch` + `git merge --ff-only` puis reload
  gracieux de gunicorn (SIGHUP). Refuse si arbre sale ou fast-forward impossible.
- **image** (conteneur, pas de `.git`) : "check" = dernière *release* GitHub comparée au
  fichier `VERSION` (semver) ; "update" = POST au sidecar `updater` (`UPDATER_URL` +
  `UPDATE_TOKEN`), qui fait `docker compose pull && up -d`. Sans jeton, le bouton affiche
  la commande manuelle.

stdlib only (urllib) — no extra dependency.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from urllib import error as urlerror
from urllib import request as urlrequest

from .storage import DATA_DIR

ROOT = os.path.dirname(os.path.dirname(__file__))
VERSION_FILE = os.path.join(ROOT, "VERSION")
CACHE_PATH = os.path.join(DATA_DIR, "version_check.json")

REPO = os.environ.get("UPDATE_REPO", "glorp-fr/s3-multi-browser")
AUTO_RELOAD = os.environ.get("UPDATE_AUTO_RELOAD", "1") == "1"

# Mode image : sidecar de mise à jour.
UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:9000")
UPDATE_TOKEN = os.environ.get("UPDATE_TOKEN", "")

# VERSION only changes on update (which reloads the process), so read it once.
try:
    with open(VERSION_FILE, "r", encoding="utf-8") as _f:
        VERSION = _f.read().strip() or "0.0.0"
except OSError:
    VERSION = "0.0.0"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _semver(s):
    """'v0.8.1', '0.8.1-rc1', '0.8' -> (0, 8, 1) / (0, 8, 0). Non numérique -> 0."""
    head = (s or "0").lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for chunk in head.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    parts += [0, 0, 0]
    return tuple(parts[:3])


def _git(*args, timeout=20):
    try:
        return subprocess.run(
            ["git", "-C", ROOT, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def git_info():
    inside = _git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return {"is_git": False, "branch": None, "commit": None,
                "commit_short": None, "commit_date": None, "dirty": False}
    branch = _git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or "HEAD"
    commit = _git("rev-parse", "HEAD").stdout.strip() or None
    date = _git("show", "-s", "--format=%cI", "HEAD").stdout.strip() or None
    dirty = bool(_git("status", "--porcelain").stdout.strip())
    return {
        "is_git": True,
        "branch": branch,
        "commit": commit,
        "commit_short": commit[:7] if commit else None,
        "commit_date": date,
        "dirty": dirty,
    }


def local_state():
    state = {"version": VERSION}
    state.update(git_info())
    return state


# --- GitHub -----------------------------------------------------------------

def _api(path):
    req = urlrequest.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "multi-s3-browser-updater",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(req, timeout=12) as resp:
        return json.load(resp)


def check_update():
    """Query GitHub, cache and return the result dict."""
    info = local_state()
    result = {
        "checked_at": _now(), "ok": False, "error": None, "mode": "git",
        "up_to_date": None, "behind_by": None, "ahead_by": None, "diverged": False,
        "remote_commit_short": None, "compare_url": None, "default_branch": None,
        "repo": REPO, "local_commit_short": info["commit_short"], "version": VERSION,
        "latest_version": None, "release_url": None,
    }
    if not info["is_git"] or not info["commit"]:
        return _check_image(result)
    try:
        repo = _api(f"/repos/{REPO}")
        branch = repo.get("default_branch") or info["branch"] or "main"
        cmp = _api(f"/repos/{REPO}/compare/{info['commit']}...{branch}")
    except urlerror.HTTPError as exc:
        if exc.code in (403, 404):
            result["error"] = (f"GitHub a répondu {exc.code} : dépôt introuvable ou privé "
                               "(définir la variable GITHUB_TOKEN) ou quota API atteint.")
        else:
            result["error"] = f"GitHub a répondu {exc.code} ({exc.reason})."
        _cache_write(result)
        return result
    except (urlerror.URLError, TimeoutError, ValueError) as exc:
        result["error"] = f"Impossible de contacter GitHub : {exc}"
        _cache_write(result)
        return result

    # GitHub compares the *head* ref against the *base* ref. Here base = local commit,
    # head = <branch>, so `ahead_by` is the number of commits the branch has that the
    # deployed checkout is missing (= how far behind we are), and `behind_by` would be
    # our local-only commits.
    behind = cmp.get("ahead_by", 0) or 0
    local_only = cmp.get("behind_by", 0) or 0
    missing = cmp.get("commits") or []
    result.update(
        ok=True,
        default_branch=branch,
        behind_by=behind,
        ahead_by=local_only,
        up_to_date=(behind == 0),
        diverged=(cmp.get("status") == "diverged"),
        remote_commit_short=(missing[-1]["sha"][:7] if missing else info["commit_short"]),
        compare_url=cmp.get("html_url") or cmp.get("permalink_url"),
    )
    _cache_write(result)
    return result


def _check_image(result):
    """Mode conteneur : compare la dernière release GitHub au fichier VERSION."""
    result["mode"] = "image"
    try:
        rel = _api(f"/repos/{REPO}/releases/latest")
    except urlerror.HTTPError as exc:
        if exc.code == 404:
            result["error"] = (f"Aucune release publiée sur {REPO} : "
                               "impossible de déterminer la dernière version.")
        elif exc.code == 403:
            result["error"] = "GitHub a répondu 403 : quota API atteint (définir GITHUB_TOKEN)."
        else:
            result["error"] = f"GitHub a répondu {exc.code} ({exc.reason})."
        _cache_write(result)
        return result
    except (urlerror.URLError, TimeoutError, ValueError) as exc:
        result["error"] = f"Impossible de contacter GitHub : {exc}"
        _cache_write(result)
        return result

    latest = (rel.get("tag_name") or "").strip().lstrip("vV")
    result.update(
        ok=True,
        latest_version=latest or None,
        release_url=rel.get("html_url"),
        up_to_date=(_semver(latest) <= _semver(VERSION)),
    )
    _cache_write(result)
    return result


# --- apply ----------------------------------------------------------------

def apply_update():
    info = local_state()
    if not info["is_git"]:
        return _apply_image()
    if info["branch"] == "HEAD":
        return {"ok": False, "changed": False,
                "message": "HEAD détaché : placez-vous sur une branche avant de mettre à jour."}
    if info["dirty"]:
        return {"ok": False, "changed": False,
                "message": "Modifications locales non commitées : committez ou remisez-les avant."}

    branch = info["branch"]
    old = info["commit"]

    fetched = _git("fetch", "--prune", "origin")
    if fetched.returncode != 0:
        return {"ok": False, "changed": False,
                "message": f"`git fetch` a échoué : {fetched.stderr.strip() or 'erreur inconnue'}"}

    merged = _git("merge", "--ff-only", f"origin/{branch}")
    if merged.returncode != 0:
        return {"ok": False, "changed": False,
                "message": "Fast-forward impossible (historique local divergent ou branche "
                           f"distante absente) : {merged.stderr.strip() or merged.stdout.strip()}"}

    new = _git("rev-parse", "HEAD").stdout.strip()
    changed = bool(new) and new != old
    msg = "Déjà à jour, aucun changement." if not changed else \
          f"Mis à jour : {old[:7]} → {new[:7]}. Rechargement de l'application…"
    if changed:
        _schedule_reload()
    check_update()  # refresh the cached banner
    return {"ok": True, "changed": changed, "old": old, "new": new, "message": msg,
            "reload": changed and _can_reload()}


_MANUAL_CMD = ("docker compose -f docker-compose.yml pull && "
               "docker compose -f docker-compose.yml up -d")


def _apply_image():
    """Mode conteneur : délègue au sidecar `updater`, ou renvoie la commande manuelle."""
    latest = (cached_check() or {}).get("latest_version")
    toward = f" vers la version {latest}" if latest else ""
    if not UPDATE_TOKEN:
        return {"ok": False, "changed": False,
                "message": (f"Instance en conteneur : mise à jour{toward} non automatisée "
                            f"(sidecar `updater` absent). Sur l'hôte : {_MANUAL_CMD}")}
    try:
        req = urlrequest.Request(
            UPDATER_URL.rstrip("/") + "/update", method="POST", data=b"{}",
            headers={"Authorization": f"Bearer {UPDATE_TOKEN}",
                     "Content-Type": "application/json"},
        )
        with urlrequest.urlopen(req, timeout=600) as resp:
            payload = json.load(resp)
    except urlerror.HTTPError as exc:
        detail = ""
        try:
            detail = " " + (json.load(exc).get("message") or "")
        except Exception:
            pass
        return {"ok": False, "changed": False,
                "message": f"Le service de mise à jour a répondu {exc.code}.{detail}"}
    except (urlerror.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "changed": False,
                "message": (f"Service de mise à jour injoignable ({exc}). "
                            f"Mise à jour manuelle : {_MANUAL_CMD}")}

    if not payload.get("ok"):
        return {"ok": False, "changed": False,
                "message": "Échec de la mise à jour : " + (payload.get("message") or "raison inconnue")}
    return {"ok": True, "changed": True, "reload": False,
            "message": (f"Mise à jour{toward or ' vers la dernière version'} lancée. "
                        "L'application redémarre — réactualisez la page dans ~20 s.")}


def _can_reload():
    return AUTO_RELOAD and "gunicorn" in sys.modules


def _schedule_reload():
    """Ask the gunicorn master to gracefully reload workers (picks up new code)."""
    if not _can_reload():
        return
    import signal
    import threading

    def _do():
        try:
            os.kill(os.getppid(), signal.SIGHUP)
        except OSError:
            pass

    threading.Timer(1.5, _do).start()


# --- cache --------------------------------------------------------------

def cached_check():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _cache_write(result):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass


def update_available():
    c = cached_check()
    if not c or not c.get("ok"):
        return False
    if c.get("mode") == "image":
        return c.get("up_to_date") is False
    return bool(c.get("behind_by"))
