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
- **Utilisateurs** : soit **administrateur** (accès total, gère providers/comptes/groupes/utilisateurs),
  soit utilisateur standard dont les accès découlent uniquement de ses **groupes**.
- **Groupes** (`Administration → Groupes`) : unité de contrôle d'accès. Un groupe couvre un ensemble
  de comptes S3 (liste explicite ou « tous les comptes ») et porte un jeu de droits fins : `download`,
  `upload`, `delete`, `bucket_admin` (créer/supprimer des buckets). La navigation (lister les buckets,
  parcourir, rechercher, recalculer la volumétrie) est implicite sur tout compte couvert. Un
  utilisateur peut appartenir à plusieurs groupes : ses accès et droits effectifs sont l'**union** de
  ses groupes. Un groupe sans droit coché = lecture seule.
- **Explorateur de buckets** par compte : navigation par "dossiers" (préfixes `/`), upload,
  téléchargement, création/suppression de dossiers et buckets, recherche d'objets dans le dossier
  courant, pagination (20/30/50/100 par page).
- **Actions groupées** (cases à cocher + « tout sélectionner ») :
  - page Buckets : actualiser la volumétrie de plusieurs buckets d'un coup (ceux calculés il y a
    moins de 24 h sont ignorés et comptabilisés dans le résumé), ou supprimer plusieurs buckets ;
  - explorateur d'objets : supprimer une sélection d'objets et de dossiers (récursif), ou la
    télécharger en une archive `.zip` (construite côté serveur).
- **Volumétrie** : taille utilisée par bucket et par compte affichée en GiB/TiB, mise en cache et
  actualisable manuellement au maximum une fois toutes les 24h (calcul coûteux car basé sur un listing
  complet du bucket).
- **Logs** (`Administration → Logs`) : journal d'audit de toutes les actions — connexions/déconnexions
  et échecs de connexion, actions S3 en lecture (listing, navigation, téléchargement) et en écriture
  (upload, création/suppression de bucket, dossier, objet), CRUD admin. Vue **temps réel** (rafraîchie
  toutes les 3 s) avec recherche, filtre par catégorie et bouton **Pause**, plus un onglet **historique
  des connexions**. Aucun secret ni mot de passe n'est journalisé. Stocké dans `data/audit.jsonl`
  (JSON Lines, plafonné à 5 Mo puis une rotation `.1`).
- **Sauvegarde de configuration** (`Administration → Sauvegarde`) : archive `data/db.json` + le
  journal d'audit dans un `.tar.gz` horodaté, envoyé vers **S3** (endpoint / AK / SK / bucket /
  préfixe) ou **SMB** (serveur / partage / sous-dossier / domaine / utilisateur / mot de passe).
  Fréquence quotidienne ou hebdomadaire, heure en **UTC**, **rétention** (N archives conservées sur
  la cible, les plus anciennes sont purgées). Le mot de passe SMB et la Secret Key S3 sont chiffrés
  au repos (Fernet, comme les comptes S3). Un minuteur interne au process rejoue la sauvegarde ;
  bouton **Sauvegarder maintenant** pour un déclenchement synchrone. Garder **un seul worker
  gunicorn** (sinon chaque worker planifie sa propre sauvegarde).
- **Version & mises à jour** (`Administration → Version`) : version courante = fichier `VERSION`
  (semver) + SHA court et date du commit du checkout. Bouton **Vérifier les mises à jour** = comparaison
  via l'API GitHub du commit local avec le dernier commit de la branche par défaut du dépôt
  (`glorp-fr/s3-multi-browser` par défaut, cf. `UPDATE_REPO`). Bouton **Mettre à jour** = `git fetch`
  puis `git merge --ff-only` sur le dossier de l'app, puis rechargement gracieux de gunicorn (SIGHUP).
  Refusé si l'arbre de travail est sale, si ce n'est pas un dépôt git, ou si le fast-forward est
  impossible. La version et un pictogramme « MAJ dispo » sont affichés en pied de barre latérale pour
  tous les utilisateurs.

## Configuration (variables d'environnement)

| Variable | Requis | Description |
|---|---|---|
| `APP_MASTER_KEY` | oui | Clé utilisée pour chiffrer les Secret Keys des comptes en base. À générer une fois et à garder stable (sa perte rend les comptes existants illisibles). |
| `FLASK_SECRET_KEY` | recommandé | Clé de signature des sessions Flask. Par défaut réutilise `APP_MASTER_KEY`. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | au premier démarrage | Crée le premier compte admin si aucun utilisateur n'existe encore. |
| `MULTI_S3_BROWSER_DATA_DIR` | non | Répertoire de stockage de `db.json`, `usage_cache.json`, `audit.jsonl` et `version_check.json` (défaut : `./data`). L'ancien nom `OOS_VIEWER_DATA_DIR` reste accepté en repli. |
| `MAX_UPLOAD_MB` | non | Taille max d'upload en Mo (défaut : 512). |
| `UPDATE_REPO` | non | Dépôt GitHub `owner/name` interrogé pour les mises à jour (défaut : `glorp-fr/s3-multi-browser`). |
| `GITHUB_TOKEN` | non | Jeton pour la vérification de mise à jour si le dépôt est privé ou pour éviter le quota API anonyme. Lecture seule (`contents:read`) suffit. |
| `UPDATE_AUTO_RELOAD` | non | `1` (défaut) : recharge gunicorn automatiquement après une mise à jour appliquée. `0` : ne recharge pas (redémarrage manuel). |

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

- Le stockage (`data/db.json`, `data/usage_cache.json`, `data/audit.jsonl`) n'est pas une base de
  données : ce sont des fichiers protégés par un verrou process-local. L'image tourne donc avec **un
  seul worker gunicorn** (multi-threadé) pour éviter toute écriture concurrente entre plusieurs
  process — largement suffisant pour un usage interne.
- Le client S3 utilise `boto3` avec un `endpoint_url` construit depuis le template du provider
  (`https://oos.{region}.outscale.com` pour Outscale, `https://s3.{region}.amazonaws.com` pour AWS, etc.).
- Aucun secret n'est jamais renvoyé au navigateur : la Secret Key saisie à la création d'un compte n'est
  plus affichée ensuite (champ vide = ne pas modifier).
- Migration automatique et transparente au démarrage (`storage.migrate()`) pour les installations
  antérieures à l'introduction des providers : les comptes existants sont rattachés à un provider
  "Outscale" recréé avec leur région d'origine.
