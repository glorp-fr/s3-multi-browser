# Multi S3 Browser

Interface web légère pour parcourir, uploader et supprimer des objets sur des stockages compatibles
S3 (Outscale OOS, AWS S3, tout autre provider S3-compatible), avec gestion multi-provider,
multi-comptes et multi-utilisateurs. Pas de base de données : tout est stocké dans un fichier JSON
chiffré côté serveur.

## Fonctionnalités

- **Providers** : Outscale et AWS préconfigurés au premier démarrage (endpoint + liste de régions), et
  possibilité d'ajouter n'importe quel provider S3-compatible (MinIO, Ceph, etc.) avec un endpoint et
  des régions personnalisés depuis **Administration → Providers**.
- **Comptes** : plusieurs comptes par provider (nom, région, Access Key, Secret Key chiffrée au repos),
  regroupés par provider dans la liste des comptes.
- **Utilisateurs** avec 3 rôles : `admin` (gère providers/comptes/utilisateurs), `operateur`
  (upload/download/delete/create sur ses comptes autorisés), `readonly` (lecture/téléchargement seuls).
- **Explorateur de buckets** par compte : navigation par "dossiers" (préfixes `/`), upload,
  téléchargement, création/suppression de dossiers et buckets, recherche d'objets dans le dossier
  courant, pagination (20/30/50/100 par page).
- **Volumétrie** : taille utilisée par bucket et par compte affichée en GiB/TiB, mise en cache et
  actualisable manuellement au maximum une fois toutes les 24h (calcul coûteux car basé sur un listing
  complet du bucket).

## Configuration (variables d'environnement)

| Variable | Requis | Description |
|---|---|---|
| `APP_MASTER_KEY` | oui | Clé utilisée pour chiffrer les Secret Keys des comptes en base. À générer une fois et à garder stable (sa perte rend les comptes existants illisibles). |
| `FLASK_SECRET_KEY` | recommandé | Clé de signature des sessions Flask. Par défaut réutilise `APP_MASTER_KEY`. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | au premier démarrage | Crée le premier compte admin si aucun utilisateur n'existe encore. |
| `MULTI_S3_BROWSER_DATA_DIR` | non | Répertoire de stockage de `db.json` et `usage_cache.json` (défaut : `./data`). L'ancien nom `OOS_VIEWER_DATA_DIR` reste accepté en repli. |
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

Ouvrir http://localhost:5000, se connecter avec `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Les providers
Outscale et AWS sont créés automatiquement au premier démarrage ; ajouter un compte depuis
**Administration → Comptes** (Access Key / Secret Key saisies via le formulaire, jamais en dur dans le code).

## Lancer avec Docker

```bash
docker build -t multi-s3-browser .
docker run -p 5000:5000 \
  -e APP_MASTER_KEY="change-me" \
  -e ADMIN_USERNAME="admin" \
  -e ADMIN_PASSWORD="change-me-too" \
  -v multi-s3-browser-data:/app/data \
  multi-s3-browser
```

Ou via `docker-compose.yml` (variables lues depuis un fichier `.env` local, non versionné) :

```bash
docker compose up --build
```

## Notes techniques

- Le stockage (`data/db.json`, `data/usage_cache.json`) n'est pas une base de données : ce sont des
  fichiers protégés par un verrou process-local. L'image tourne donc avec **un seul worker gunicorn**
  (multi-threadé) pour éviter toute écriture concurrente entre plusieurs process — largement suffisant
  pour un usage interne.
- Le client S3 utilise `boto3` avec un `endpoint_url` construit depuis le template du provider
  (`https://oos.{region}.outscale.com` pour Outscale, `https://s3.{region}.amazonaws.com` pour AWS, etc.).
- Aucun secret n'est jamais renvoyé au navigateur : la Secret Key saisie à la création d'un compte n'est
  plus affichée ensuite (champ vide = ne pas modifier).
- Migration automatique et transparente au démarrage (`storage.migrate()`) pour les installations
  antérieures à l'introduction des providers : les comptes existants sont rattachés à un provider
  "Outscale" recréé avec leur région d'origine.
