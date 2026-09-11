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
  `upload`, `delete`, `bucket_admin` (créer/supprimer des buckets, éditer leur configuration —
  versionning/lock/lifecycle/policy/ACL). La navigation (lister les buckets,
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
- **Création de bucket** : formulaire dépliable (case « Versionning », case « Object Lock » — avec
  rétention par défaut optionnelle — et une règle de lifecycle optionnelle), pour poser ces réglages
  dès la création plutôt que de revenir ensuite dans *Configurer*. Cocher Object Lock active
  automatiquement le versionning (imposé par S3) ; c'est le seul réglage qui **ne peut pas** être
  ajouté après coup, contrairement aux autres.
- **Configuration de bucket** (bouton *Configurer*, droit `bucket_admin`) : édition graphique
  (cases à cocher / champs, pas de JSON à écrire à la main sauf pour la policy) de 5 réglages S3,
  chacun affichant son état actuel et annulable indépendamment (un niveau d'undo, état restauré
  tel qu'il était juste avant le dernier « Enregistrer ») :
  - **Versionning** : activé / suspendu (irréversible vers « jamais activé », limitation S3) ;
  - **Object Lock** : rétention par défaut (mode Gouvernance/Conformité + durée) si le bucket a
    été créé avec le verrouillage activé (non activable après coup, limitation S3) ;
  - **Lifecycle** : règles ajoutables/supprimables (préfixe, expiration des objets, expiration
    des versions précédentes, nettoyage des uploads multipart incomplets) ;
  - **Bucket policy** et **ACL** : présentées en deux colonnes — la version actuelle en lecture à
    droite, le formulaire d'édition à gauche, avec un bouton **Copier la version actuelle** qui
    préremplit la nouvelle valeur à partir de l'actuelle (pour l'ACL, seulement si l'actuelle
    correspond à une valeur prédéfinie reconnue — sinon le bouton est désactivé, l'ACL courante
    étant des grants personnalisés non représentables tels quels). Policy en éditeur JSON IAM brut ;
    ACL = une valeur prédéfinie (`private`, `public-read`, …, avec confirmation avant tout accès
    public) **et/ou** des accès par compte ciblés (ID canonique S3 ou email, une permission par
    accès ajouté), les deux combinés en un seul appel S3.
- **Synchronisation entre buckets** (`/sync`) : jobs planifiés de copie objet / préfixe / bucket
  entier vers un autre bucket, **cross-compte et cross-provider** (les objets transitent par le
  serveur en flux, jamais bufferisés). Suppression des objets absents de la source **optionnelle**
  par job (décochée par défaut, ce n'est pas un miroir strict imposé). Accessible à tout utilisateur
  disposant de `download` sur le compte source et `upload` sur le compte destination (pas
  admin-only) ; chaque utilisateur gère ses propres jobs, **Administration → Synchronisation**
  offre une vue globale pour superviser/désactiver/reprendre les jobs de tous. Droits revérifiés à
  chaque exécution planifiée : perte d'un droit nécessaire ⇒ job désactivé automatiquement
  (`rights_error`), débloqué par un admin (reprise) ou par le propriétaire (ré-édition). La barre
  d'actions groupées de l'explorateur propose aussi **Copier vers…**, qui crée un job one-shot en
  tâche de fond suivi sur `/sync` (barre de progression, polling). Sélection de compte/bucket en
  liste déroulante et navigateur de préfixe intégré (bouton **Parcourir…**) plutôt que de la saisie
  libre.
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
  **Restauration** : la page liste les archives disponibles sur la destination configurée
  (S3 ou SMB) ; choisir une archive et confirmer remplace immédiatement `db.json` **et** le
  journal d'audit par son contenu (comptes, utilisateurs, groupes, jobs de synchronisation,
  config de sauvegarde inclus). Action destructrice pour la config en cours, confirmée
  explicitement ; une copie de ce qui est remplacé est gardée localement dans
  `data/pre-restore-backup/` (jamais envoyée nulle part) pour pouvoir revenir en arrière à la
  main en cas d'erreur.
- **Version & mises à jour** (`Administration → Version`) : version courante = fichier `VERSION`
  (semver). Deux modes selon le déploiement :
  - **checkout git** (gunicorn sur l'hôte) : *Vérifier* compare le commit local au dernier commit de
    la branche par défaut du dépôt (`UPDATE_REPO`, défaut `glorp-fr/s3-multi-browser`) ; *Mettre à
    jour* fait `git fetch` + `git merge --ff-only` + rechargement gracieux de gunicorn (SIGHUP).
    Refusé si l'arbre est sale ou si le fast-forward est impossible.
  - **image** (conteneur, pas de `.git`) : *Vérifier* compare `VERSION` à la dernière **release**
    GitHub ; *Mettre à jour* délègue au sidecar `updater` (`docker compose pull && up -d`). Sans le
    sidecar, le bouton affiche la commande à lancer sur l'hôte.
  La version et un pictogramme « MAJ dispo » sont affichés en pied de barre latérale pour tous les
  utilisateurs.

## Configuration (variables d'environnement)

| Variable | Requis | Description |
|---|---|---|
| `APP_MASTER_KEY` | oui | Clé utilisée pour chiffrer les Secret Keys des comptes en base. À générer une fois et à garder stable (sa perte rend les comptes existants illisibles). |
| `FLASK_SECRET_KEY` | recommandé | Clé de signature des sessions Flask. Par défaut réutilise `APP_MASTER_KEY`. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | au premier démarrage | Crée le premier compte admin si aucun utilisateur n'existe encore. |
| `MULTI_S3_BROWSER_DATA_DIR` | non | Répertoire de stockage de `db.json`, `usage_cache.json`, `audit.jsonl` et `version_check.json` (défaut : `./data`). L'ancien nom `OOS_VIEWER_DATA_DIR` reste accepté en repli. |
| `MAX_UPLOAD_MB` | non | Taille max d'upload en Mo (défaut : 512). |
| `UPDATE_REPO` | non | Dépôt GitHub `owner/name` interrogé pour les mises à jour — commits (mode git) ou releases (mode image). Défaut : `glorp-fr/s3-multi-browser`. |
| `GITHUB_TOKEN` | non | Jeton pour la vérification de mise à jour si le dépôt est privé ou pour éviter le quota API anonyme. Lecture seule (`contents:read`) suffit. |
| `UPDATE_AUTO_RELOAD` | non | Mode git : `1` (défaut) recharge gunicorn automatiquement après une mise à jour ; `0` = redémarrage manuel. |
| `UPDATE_TOKEN` | non | Mode image : jeton partagé avec le sidecar `updater`. Vide = bouton *Mettre à jour* remplacé par la commande manuelle. |
| `UPDATER_URL` | non | Mode image : URL du sidecar (défaut `http://updater:9000`, résolu sur le réseau compose). |

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

Image publiée sur GHCR à chaque tag `vX.Y.Z` : `ghcr.io/glorp-fr/s3-multi-browser` (`linux/amd64`),
tags `vX.Y.Z`, `X.Y`, `latest`.

```bash
docker run -p 5000:5000 \
  -e APP_MASTER_KEY="change-me" \
  -e ADMIN_USERNAME="admin" \
  -e ADMIN_PASSWORD="change-me-too" \
  -v multi-s3-browser-data:/app/data \
  ghcr.io/glorp-fr/s3-multi-browser:latest
```

### docker compose

`cp .env.example .env`, renseigner, puis :

```bash
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d
```

En développement, `docker compose up --build` suffit : `docker-compose.override.yml` (fusionné
automatiquement) reconstruit l'image localement et laisse le sidecar `updater` de côté.

### Mise à jour depuis l'interface (sidecar `updater`)

Le `docker-compose.yml` inclut un service `updater` qui permet le bouton *Mettre à jour* de
`Administration → Version` : l'app le contacte sur le réseau interne, il exécute
`docker compose pull` puis `up -d`.

> ⚠️ **Compromis de sécurité.** `updater` monte `/var/run/docker.sock` : accès Docker = équivalent
> root sur l'hôte. Le risque est circonscrit — le conteneur n'a **aucun code applicatif**, **aucun
> port publié**, vit sur un réseau `internal` (pas d'accès entrant hôte ni sortie internet), et le
> seul endpoint est protégé par `UPDATE_TOKEN` (`openssl rand -hex 32`). L'app applicative, elle, ne
> voit jamais le socket. Pour refuser ce compromis : retirer le service `updater` et laisser
> `UPDATE_TOKEN` vide — les mises à jour se font alors à la main
> (`docker compose -f docker-compose.yml pull && up -d`).

### Publier une version

Bump `VERSION`, commit, puis :

```bash
git tag v$(cat VERSION) && git push origin v$(cat VERSION)
```

Le workflow `.github/workflows/release.yml` vérifie que le tag correspond à `VERSION`, construit et
pousse les images app + `updater`, et crée la GitHub Release lue par les instances en mode image.

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
