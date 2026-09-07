#!/usr/bin/env python3
"""Sidecar de mise à jour pour Multi S3 Browser.

Seul ce conteneur voit le socket Docker. Il n'expose qu'un endpoint, protégé par un
jeton partagé, et n'exécute que deux commandes fixes — `docker compose pull` puis
`docker compose up -d` sur le projet compose monté en lecture seule dans /work.
Rien du corps de la requête n'est utilisé : pas de surface d'injection.

Environnement :
  UPDATE_TOKEN   (requis) jeton attendu dans « Authorization: Bearer <token> »
  COMPOSE_DIR    (défaut /work)                 dossier contenant le fichier compose
  COMPOSE_FILE   (défaut docker-compose.yml)    fichier compose à utiliser
  LISTEN_PORT    (défaut 9000)
"""
import hmac
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("UPDATE_TOKEN", "")
COMPOSE_DIR = os.environ.get("COMPOSE_DIR", "/work")
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose.yml")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9000"))

_lock = threading.Lock()


def _compose(*args, timeout):
    return subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        cwd=COMPOSE_DIR, capture_output=True, text=True, timeout=timeout,
    )


def _run_update():
    """pull puis up -d. Retourne (ok, texte)."""
    if not _lock.acquire(blocking=False):
        return False, "Une mise à jour est déjà en cours."
    try:
        pull = _compose("pull", timeout=600)
        if pull.returncode != 0:
            return False, "docker compose pull a échoué :\n" + (pull.stderr or pull.stdout)
        up = _compose("up", "-d", timeout=600)
        if up.returncode != 0:
            return False, "docker compose up -d a échoué :\n" + (up.stderr or up.stdout)
        return True, "Images tirées et services recréés."
    except subprocess.TimeoutExpired:
        return False, "Délai dépassé pendant la mise à jour."
    except OSError as exc:
        return False, f"Impossible d'exécuter docker compose : {exc}"
    finally:
        _lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "msb-updater"

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix) or not TOKEN:
            return False
        return hmac.compare_digest(header[len(prefix):], TOKEN)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/update":
            self._send(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._send(403, {"ok": False, "error": "jeton invalide"})
            return
        ok, message = _run_update()
        self._send(200 if ok else 500, {"ok": ok, "message": message})

    def log_message(self, fmt, *args):  # journalise sur stdout sans le bruit par défaut
        print("updater: " + (fmt % args))


def main():
    if not TOKEN:
        raise SystemExit("updater: UPDATE_TOKEN non défini — refus de démarrer.")
    srv = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"updater: écoute sur :{LISTEN_PORT}, projet {COMPOSE_DIR}/{COMPOSE_FILE}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
