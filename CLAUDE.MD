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

### Application de SECURITE.MD — Module « Logs »

`SECURITE.MD` (à la racine de `~/claude`) décrit un socle de modules attendus pour toute app.
Application **module par module** ; premier module livré : **Logs**.

- **Journal d'audit** : `app/audit.py` — fichier **JSON Lines** append-only `data/audit.jsonl`
  (même esprit que `storage.py` / `usage_cache.py` : `threading.Lock` + fichiers simples, pas de
  base). Chaque événement porte un `seq` entier strictement croissant (curseur pour le live tail),
  l'horodatage UTC, la catégorie, l'action, l'acteur, l'IP (`X-Forwarded-For` en best-effort — pas
  encore de `ProxyFix`), le user-agent, le `status` (`ok`/`fail`) et une cible. Fichier **plafonné
  à 5 Mo** puis **une** rotation (`audit.jsonl.1`). `audit.log(...)` n'échoue jamais (le logging ne
  doit pas casser une requête). **Aucun secret ni mot de passe** n'est journalisé (vérifié par test).
- **Catégories journalisées** (choix validé avec l'utilisateur) :
  - `auth` — connexions, déconnexions, **échecs** de connexion (login + IP + user-agent).
  - `s3_write` — création/suppression de bucket, upload, mkdir, suppression objet/dossier
    (succès **et** échecs `ClientError`).
  - `s3_read` — listing buckets, navigation dans un bucket (+ terme de recherche), téléchargement,
    recalcul de volumétrie.
  - `admin` — CRUD providers / comptes S3 / utilisateurs (jamais le contenu des secrets/mdp, juste
    « Secret Key changée » / « mot de passe changé »).
- **Page `Administration → Logs`** (`/admin/logs`, `admin_logs.html`, lien sidebar icône `activity`) :
  - Onglet **« Journal applicatif »** : vue temps réel (`<div id="log-view">`), polling JS toutes
    les **3 s** de `GET /admin/logs/tail?after=<seq>&q=<recherche>&cat=<csv>` (JSON
    `{records, last_seq}`), lignes ajoutées en bas, auto-scroll seulement si déjà en bas. Champ de
    **recherche** (sous-chaîne, debounce 300 ms, filtrée côté serveur), cases à cocher de **filtre
    par catégorie**, bouton **Pause / Reprendre** qui fige l'affichage (= « bouton pour stopper le
    défilement » de SECURITE.MD). Rendu des lignes JS en `textContent` (pas d'injection HTML depuis
    le contenu des logs).
  - Onglet **« Historique des connexions »** : table server-rendered des événements `auth`
    (date, utilisateur, IP, résultat, événement, user-agent) avec son propre champ de filtre
    (`?q=`, rechargement page, `?tab=conn` pour rouvrir l'onglet).
- **Icônes** ajoutées à `_macros.html` : `activity`, `pause`.
- **Tests** (scripts client de test Flask + S3 mocké, non versionnés) : écriture/lecture/curseur
  `audit`, login OK/KO + logout journalisés, page `/admin/logs` + endpoint `tail` (filtres `q` et
  `cat`), CRUD admin audité, et instrumentation S3 (buckets/objects/download/create/mkdir/upload/
  delete objet+dossier/delete bucket) — plus vérif qu'aucun secret ne fuit dans les logs.

### Application de SECURITE.MD — Module « Version & mises à jour »

Choix validés avec l'utilisateur : source de version = **fichier `VERSION`** (semver) + SHA git ;
détection de MAJ = **dernier commit de la branche par défaut** sur GitHub ; bouton de MAJ =
**`git pull --ff-only` + reload gunicorn automatique**.

- **`app/version.py`** (stdlib uniquement, pas de nouvelle dépendance — `urllib`) :
  - `VERSION` = fichier `VERSION` à la racine, lu **une fois** à l'import (ne change qu'à une MAJ,
    qui recharge le process). `local_state()` = version + `git_info()` (branche, commit, date, arbre
    sale) via `git -C <root>`.
  - `check_update()` : `GET /repos/{REPO}` pour la branche par défaut, puis
    `GET /repos/{REPO}/compare/{local}...{branche}` → `behind_by`, SHA distant, lien de comparaison.
    `REPO` = env `UPDATE_REPO` (défaut `glorp-fr/s3-multi-browser`). En-tête `Authorization: Bearer`
    si `GITHUB_TOKEN` défini (dépôt privé / quota). Erreurs réseau/HTTP (403/404) rattrapées en
    message lisible. Résultat mis en cache dans `data/version_check.json`.
  - `apply_update()` : garde-fous (pas un dépôt git / HEAD détaché / arbre sale / ff impossible →
    refus explicite), sinon `git fetch --prune` + `git merge --ff-only origin/<branche>`, puis
    `_schedule_reload()` = `SIGHUP` au master gunicorn **1,5 s après** la réponse (worker recréé =
    nouveau code chargé, on n'utilise pas `--preload`). Reload conditionné à `"gunicorn" in
    sys.modules` et `UPDATE_AUTO_RELOAD != "0"`.
- **Routes** (`admin_routes.py`, `@role_required("admin")`) : `GET /admin/version` (page),
  `POST /admin/version/check`, `POST /admin/version/update`. Les deux POST sont **audités**
  (`admin` / `version_check` / `version_update`, avec `old → new`).
- **UI** : `admin_version.html` (carte version + git, bloc résultat coloré à jour / en retard /
  erreur, bouton « Mettre à jour » affiché seulement si `behind_by > 0` et arbre propre). Pied de
  **barre latérale** : `v<version>` + pastille « MAJ dispo » (lue du cache) visible **pour tous les
  utilisateurs** (exigence page user de SECURITE.MD). Icône `tag` ajoutée à `_macros.html`.
- **`Dockerfile`** : `COPY VERSION .`. En conteneur il n'y a pas de `.git` → `apply_update()`
  renvoie le message « instance non gérée par git » (comportement attendu ; MAJ = rebuild d'image).
- **Contexte Jinja** : `app/__init__.py` injecte `app_version` et `update_available` dans tous les
  templates.
- **Tests** (script non versionné) : lecture `VERSION`, `git_info`, `check_update` (en retard / à
  jour / HTTPError 404), garde-fous `apply_update` (arbre sale, pas de git, ff impossible), happy
  path avec git mocké (enchaînement fetch → merge --ff-only), routes admin + audit, version visible
  en sidebar pour un non-admin.

**Reste à appliquer de SECURITE.MD** (dans l'ordre convenu) : couche **groupes** (un groupe porte
un rôle + une liste de comptes S3 ; utilisateurs rattachés à des groupes), **HTTPS / Let's Encrypt**
(approche non tranchée : Caddy dans le compose vs TLS géré en amont), **désactivation de compte**
(vs suppression), **module de sauvegarde de configuration** (SMB / S3, planification, rétention).

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
  audit.py             # journal d'audit JSONL (data/audit.jsonl), log() / tail() / connection_history()
  version.py           # VERSION + git_info(), check_update() (API GitHub), apply_update() (git ff + SIGHUP)
  s3client.py           # boto3 client, endpoint = provider.endpoint_template.format(region=...)
  auth.py                # session, login_required, role_required, can_write, accessible_accounts
  routes/
    auth_routes.py       # /login /logout  (+ audit auth)
    admin_routes.py       # /admin/providers, /admin/accounts, /admin/users (CRUD, audités)
                          #   + /admin/logs (+ /logs/tail), /admin/version (+ /version/check, /version/update)
    explorer_routes.py     # /accounts, buckets, objets, upload/download/delete, usage/refresh (audités)
  templates/             # Jinja2, un template par page + _macros.html (icônes SVG inline)
                          #   admin_logs.html (journal live + connexions), admin_version.html
  static/style.css        # CSS transcrit de CHARTE_GRAPHIQUE.MD (+ styles .log-view / .tabs / .ver-*)
VERSION                 # numéro de version semver, affiché dans l'UI, COPY dans l'image Docker
data/                   # gitignored — db.json (chiffré), usage_cache.json, audit.jsonl(.1), version_check.json
```

## Déploiement / validation

- Testé en local sur cette machine avec `gunicorn` (pas encore de container Docker construit ni
  de dépôt GitHub créé — validation manuelle en cours avant de passer à l'étape container/push).
- Chaque évolution ci-dessus a été vérifiée par des tests automatisés (client de test Flask,
  appels S3 mockés) avant d'être considérée terminée : login, CRUD comptes/utilisateurs/providers,
  contrôle d'accès par rôle (403 serveur), recherche/pagination, calcul et cache de volumétrie,
  limite de rafraîchissement 24h, migration des données existantes.
