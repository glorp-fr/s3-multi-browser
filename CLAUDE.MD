Projet de développement d'un interface graphique pour le service OOS de outscale (stockage object compatible AWS S3)


Pourquoi?

A ce jour Outsclae ne propose pas d'interface graphique pour son service OOS.
Les utilisateurs dovent utiliser des produits Tiers tels que Cyberduck.
Il est compliqué de gérer le multi compte avec ce genre de produits.



Cible:

Produire un container  embarquant:
Un client S3
Une interface html permettant de visualiser / supprimer / ajouter des objets dans les buckets

Fonctionnalités:
- Page admin avec:
  - Gestion de plusieurs comptes OUTSCALE
    - nom du compte et ou accountid
    - AK /SK du compte (stockage du SK chiffré)
  - Gestion des utilisateur du produit avec droits d'acès aux comptes, upload, download, delete, create

- Page user avec:
  - Liste des comptes selon droits
  - Explorateur de bucket
  - Possibilité d'action selon droits du user


Chharte graphique détaillée dans le fichier CHATRE_GRAPHIQUE.MD ala racine du projet.

Stockage du produit:
Upload vers le repos github qui sera créé pour l'occasion

Les technos utilisées doivent etre tres light, pas de base de données par exemple.


---

## Journal des évolutions (tenu à jour au fil des sessions Claude Code)

### Renommage : S3 Viewer → Multi S3 Browser

Renommage complet du produit (aucun déploiement en place ni dépôt GitHub, donc rename propre) :

- **Nom affiché dans l'UI** : `Multi S3 Browser` (header, `<title>`, page de login, attributs `alt`
  du logo) — templates `base.html`, `login.html`, `accounts.html`.
- **Dossier du projet** : `projets/oos-viewer` → `projets/multi-s3-browser` (dépôt git local
  déplacé avec, historique conservé, toujours pas de remote).
- **Variable d'environnement** : `OOS_VIEWER_DATA_DIR` → `MULTI_S3_BROWSER_DATA_DIR`. L'ancien nom
  reste accepté **en repli** dans `storage.py` et `usage_cache.py`
  (`os.environ.get("MULTI_S3_BROWSER_DATA_DIR") or os.environ.get("OOS_VIEWER_DATA_DIR") or <défaut>`).
- **Docker** : service `oos-viewer` → `multi-s3-browser`, volume `oos-viewer-data` →
  `multi-s3-browser-data` (`docker-compose.yml`), `ENV` du `Dockerfile`, nom d'image dans le README
  (`docker build -t multi-s3-browser .`).

> Historique du 1er renommage (OOS Viewer → S3 Viewer) : le produit n'est plus limité à Outscale,
> il gère plusieurs **providers** S3-compatibles (Outscale, AWS, custom).

### Étape 1 — Scaffold initial (voir commit `72418ff`)

Stack retenue : **Python 3 + Flask**, templates Jinja2 server-rendered, CSS pur (pas de framework
JS, pas de build step) — cohérent avec la contrainte "techno très light, pas de base de données".

- **Auth** : session Flask, mots de passe hashés (`werkzeug.security`), décorateurs
  `login_required` / `role_required` (`app/auth.py`).
- **Rôles** : `admin` (gère providers/comptes/utilisateurs), `operateur` (upload/download/
  delete/create sur ses comptes autorisés), `readonly` (lecture/téléchargement seuls). Chaque
  action d'écriture est bloquée **côté serveur** (pas seulement caché côté UI), y compris pour un
  utilisateur qui appellerait directement une route.
- **Stockage** : pas de base de données — un fichier JSON (`data/db.json`) protégé par un verrou
  `threading.Lock` + écriture atomique (fichier temporaire puis `os.replace`). Le Secret Key de
  chaque compte est chiffré au repos avec **Fernet** (`cryptography`), clé dérivée de la variable
  d'env `APP_MASTER_KEY` (SHA-256 puis base64). Les mots de passe utilisateurs sont hashés, jamais
  stockés en clair.
- **Premier admin** : bootstrap automatique au démarrage si `data/db.json` n'a aucun utilisateur,
  à partir des variables d'env `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
- **Explorateur de buckets** : navigation par préfixe `/` (façon dossiers), upload multi-fichiers,
  téléchargement, création de bucket/dossier, suppression (objet, "dossier" = suppression
  récursive de tous les objets sous le préfixe, ou bucket entier).
- **Charte graphique** : CSS transcrit depuis `CHARTE_GRAPHIQUE.MD` (variables couleur, polices
  Google Fonts Montserrat/Open Sans/DM Mono, boutons `.btn.blue/orange/red/ghost`, badges
  `.bdg.admin/operateur/readonly`, layout header/sidebar). Logo et favicon extraits du base64
  embarqué dans ce même fichier (`app/static/assets/logo.png`, `favicon.png`).
- **Container** : `Dockerfile` (python:3.11-slim + gunicorn, **1 seul worker multi-threadé** — le
  stockage JSON n'est pas conçu pour des écritures concurrentes entre plusieurs process),
  `docker-compose.yml`, volume pour `data/`.

### Étape 2 — Volumétrie, recherche, pagination

- **Volumétrie par bucket et par compte** (GiB ou TiB) : `app/usage_cache.py`, cache séparé
  (`data/usage_cache.json`, même pattern lock + écriture atomique que `db.json`). Le calcul liste
  **tous** les objets d'un bucket (opération coûteuse) — donc jamais recalculé automatiquement au
  chargement d'une page. Un bouton "Actualiser" par bucket déclenche le calcul, mais est
  **bloqué côté serveur si le dernier calcul date de moins de 24h** (bouton grisé côté UI avec la
  date de prochaine actualisation possible en tooltip). La page Comptes agrège la volumétrie des
  buckets déjà calculés pour chaque compte ; la page Buckets d'un compte affiche le détail par
  bucket + un total agrégé.
- **Recherche d'objets** : barre de recherche dans l'explorateur, filtrant par nom (sous-chaîne,
  insensible à la casse). **Scope volontairement limité au dossier courant** (pas de scan
  récursif de tout le bucket) pour rester rapide et prévisible — à étendre si un besoin de
  recherche transverse à tout le bucket apparaît.
- **Pagination** de la liste d'objets d'un dossier : 20 lignes par page par défaut, sélecteur
  20/30/50/100 (max 100). Pagination faite en mémoire après un listing complet du préfixe courant
  (pas de pagination via `ContinuationToken` côté S3) — suffisant pour un outil "light", mais pas
  conçu pour des dossiers à plusieurs millions d'objets.

### Étape 3 — Multi-provider (Outscale / AWS / custom)

- **Nouvelle entité "Provider"** (`app/storage.py`) : `{id, name, endpoint_template, regions[]}`.
  `endpoint_template` doit contenir le paramètre `{region}` (ex.
  `https://oos.{region}.outscale.com` pour Outscale, `https://s3.{region}.amazonaws.com` pour
  AWS). `app/s3client.py` construit l'URL d'endpoint boto3 à partir de ce template + de la région
  choisie sur le compte.
- **Providers créés par défaut** au premier démarrage (`DEFAULT_PROVIDERS` dans `storage.py`) :
  **Outscale** (régions eu-west-2, us-east-2, us-west-1, ap-northeast-1, cloudgouv-eu-west-1) et
  **AWS** (16 régions standard). Entièrement éditables/supprimables ensuite depuis
  **Administration → Providers** — ce ne sont que des valeurs de départ, pas des constantes figées
  dans le code.
- **Admin → Providers** : CRUD complet (nom, endpoint avec `{region}`, régions saisies une par
  ligne dans un textarea). Un provider utilisé par au moins un compte **ne peut pas être
  supprimé** (bouton désactivé + erreur serveur si contournée).
- **Compte rattaché à un provider** : le formulaire de compte (admin) propose un `<select>`
  provider, puis un `<select>` région **peuplé dynamiquement en JS** avec les régions du provider
  choisi (pas de rechargement de page). Validation serveur : la région doit appartenir à la liste
  du provider sélectionné.
- **Regroupement par provider** dans les listings :
  - Page utilisateur "Comptes" (`explorer.accounts`) : comptes groupés sous un titre par provider.
  - Page admin "Comptes" : une colonne "Provider" (pas de regroupement visuel, la table reste à
    plat — plus adapté à la gestion qu'à la navigation).
- **Migration automatique** (`storage.migrate()`, appelée une fois au démarrage de l'app) : pour
  toute installation antérieure à cette fonctionnalité, les comptes existants sans `provider_id`
  sont rattachés à un provider "Outscale" recréé avec leur région d'origine ajoutée à sa liste de
  régions. Testé avec un vrai compte pré-existant (`data/db.json` local, compte "Test JTT SNC",
  région `cloudgouv-eu-west-1`) : migration transparente, secret déjà chiffré non touché.

## Fichiers clés

```
app/
  storage.py          # JSON db (users/accounts/providers), chiffrement Fernet, migrate()
  usage_cache.py       # cache volumétrie (data/usage_cache.json), règle des 24h, format_size()
  s3client.py           # boto3 client, endpoint = provider.endpoint_template.format(region=...)
  auth.py                # session, login_required, role_required, can_write, accessible_accounts
  routes/
    auth_routes.py       # /login /logout
    admin_routes.py       # /admin/providers, /admin/accounts, /admin/users (CRUD)
    explorer_routes.py     # /accounts, buckets, objets, upload/download/delete, usage/refresh
  templates/             # Jinja2, un template par page + _macros.html (icônes SVG inline)
  static/style.css        # CSS transcrit de CHARTE_GRAPHIQUE.MD
data/                   # gitignored — db.json (chiffré) + usage_cache.json, créés au runtime
```

## Déploiement / validation

- Testé en local sur cette machine avec `gunicorn` (pas encore de container Docker construit ni
  de dépôt GitHub créé — validation manuelle en cours avant de passer à l'étape container/push).
- Chaque évolution ci-dessus a été vérifiée par des tests automatisés (client de test Flask,
  appels S3 mockés) avant d'être considérée terminée : login, CRUD comptes/utilisateurs/providers,
  contrôle d'accès par rôle (403 serveur), recherche/pagination, calcul et cache de volumétrie,
  limite de rafraîchissement 24h, migration des données existantes.
