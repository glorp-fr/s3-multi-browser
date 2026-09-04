# OOS Viewer

Interface web légère pour parcourir, uploader et supprimer des objets sur le service **OOS** d'Outscale
(stockage compatible S3), avec gestion multi-comptes et multi-utilisateurs. Pas de base de données :
tout est stocké dans un fichier JSON chiffré côté serveur.

## Fonctionnalités

- Gestion de plusieurs comptes OUTSCALE (nom, région, Access Key, Secret Key chiffrée au repos).
- Gestion des utilisateurs avec 3 rôles : `admin` (gère comptes + utilisateurs), `operateur` (upload/download/delete/create sur ses comptes autorisés), `readonly` (lecture/téléchargement seuls).
- Explorateur de buckets par compte : navigation par "dossiers" (préfixes `/`), upload, téléchargement, création/suppression de dossiers et buckets.

## Configuration (variables d'environnement)

| Variable | Requis | Description |
|---|---|---|
| `APP_MASTER_KEY` | oui | Clé utilisée pour chiffrer les Secret Keys des comptes en base. À générer une fois et à garder stable (sa perte rend les comptes existants illisibles). |
| `FLASK_SECRET_KEY` | recommandé | Clé de signature des sessions Flask. Par défaut réutilise `APP_MASTER_KEY`. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | au premier démarrage | Crée le premier compte admin si aucun utilisateur n'existe encore. |
| `OOS_VIEWER_DATA_DIR` | non | Répertoire de stockage de `db.json` (défaut : `./data`). |
| `MAX_UPLOAD_MB` | non | Taille max d'upload en Mo (défaut : 512). |

## Lancer en local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export APP_MASTER_KEY="change-me"
export ADMIN_USERNAME="admin"
export ADMIN_PASSWORD="change-me-too"

flask --app wsgi run --debug
```

Ouvrir http://localhost:5000, se connecter avec `ADMIN_USERNAME` / `ADMIN_PASSWORD`, puis ajouter un
compte OUTSCALE (Access Key / Secret Key) depuis **Administration → Comptes OUTSCALE**.

## Lancer avec Docker

```bash
docker build -t oos-viewer .
docker run -p 5000:5000 \
  -e APP_MASTER_KEY="change-me" \
  -e ADMIN_USERNAME="admin" \
  -e ADMIN_PASSWORD="change-me-too" \
  -v oos-viewer-data:/app/data \
  oos-viewer
```

Ou via `docker-compose.yml` (variables lues depuis un fichier `.env` local, non versionné) :

```bash
docker compose up --build
```

## Notes techniques

- Le stockage (`data/db.json`) n'est pas une base de données : c'est un fichier protégé par un verrou
  process-local. L'image tourne donc avec **un seul worker gunicorn** (multi-threadé) pour éviter toute
  écriture concurrente entre plusieurs process — largement suffisant pour un usage interne.
- Le client S3 utilise `boto3` avec `endpoint_url=https://oos.<region>.outscale.com` (SigV4).
- Aucun secret n'est jamais renvoyé au navigateur : la Secret Key saisie à la création d'un compte n'est
  plus affichée ensuite (champ vide = ne pas modifier).
