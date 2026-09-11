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


Charte graphique détaillée dans `~/claude/CHARTE_GRAPHIQUE.md` (référence commune à toutes les apps).

Stockage du produit:
Upload vers le repos github qui sera créé pour l'occasion

Les technos utilisées doivent etre tres light, pas de base de données par exemple.


---

## Journal des évolutions (tenu à jour au fil des sessions Claude Code)

### Fix : ACL prédéfinie invalide sur PutBucketAcl (v0.9.2)

Bug remonté par l'utilisateur en testant v0.9.1 en réel : `bucket-owner-read` et
`bucket-owner-full-control` — présentes dans `CANNED_ACLS` depuis la v0.9.0 — provoquaient
`InvalidArgument` sur `PutBucketAcl`. Ces deux valeurs ne sont valides que pour `PutObjectAcl` /
`CopyObject` (ACL **objet**), jamais pour une ACL de **bucket** — confirmé via
`botocore` (`operation_model('PutBucketAcl').input_shape.members['ACL'].enum` ne liste que
`private`, `public-read`, `public-read-write`, `authenticated-read`). Retirées de
`bucket_config.CANNED_ACLS` ; les 4 valeurs restantes réappliquées de bout en bout en test
(moto) pour confirmer qu'aucune ne renvoie plus d'erreur.

### Options à la création de bucket + policy/ACL en deux colonnes + sidebar (v0.9.1)

Suite directe de la v0.9.0, choix validés avec l'utilisateur avant implémentation : le formulaire
de création de bucket bascule en formulaire dépliable (case à cocher) proposant versionning,
verrouillage et une règle de lifecycle dès la création — plutôt que de forcer un aller-retour par
*Configurer* juste après. Et pour policy/ACL, remplacement de l'affichage empilé par une mise en
page à deux colonnes (actuelle en lecture à droite, édition à gauche) avec bouton **Copier la
version actuelle**.

- **`app/routes/explorer_routes.py`** (`bucket_new`) : `lock` cochée ⇒
  `create_bucket(..., ObjectLockEnabledForBucket=True)` (le seul réglage qui ne peut être posé
  qu'à la création, S3 ne permet pas de l'ajouter après coup) et active automatiquement le
  versionning (imposé par S3 sur un bucket avec Object Lock — géré aussi côté JS : la case
  versionning se coche et se désactive quand on coche verrouillage). Rétention par défaut et règle
  de lifecycle appliquées après la création via les mêmes `bucket_config.set_*` que la page
  Configurer ; un échec sur l'une de ces étapes est signalé par un flash mais n'annule pas la
  création (le bucket existe déjà, l'admin corrige via Configurer).
- **`app/bucket_config.py`** : `detect_canned_acl(snapshot)` — reconnaissance best-effort d'une ACL
  prédéfinie à partir des grants bruts (`private`/`public-read`/`public-read-write`/
  `authenticated-read`, en excluant le grant FULL_CONTROL implicite du propriétaire) ; renvoie
  `None` si les grants sont personnalisés (cross-account, etc.) — le bouton « Copier » de l'onglet
  ACL est alors désactivé, avec l'ACL actuelle affichée à droite pour référence mais non copiable
  telle quelle dans le menu déroulant prédéfini de gauche.
- **`bucket_config.html`** : nouvelle classe `.split-view` (deux colonnes, `flex-wrap` pour rester
  utilisable en dessous de ~800px) réutilisée pour les onglets Policy et ACL uniquement (les 3
  autres onglets gardent l'affichage empilé existant, qui montre déjà l'état courant juste au-dessus
  du formulaire). Bouton « Copier » : JS pur, recopie le `<pre>` de droite (policy) ou pré-sélectionne
  la valeur détectée (ACL) — aucun aller-retour serveur.
- **`buckets.html`** : le simple champ nom devient un bouton qui déplie un `form-card` (nom +
  versionning + lock [+ rétention par défaut optionnelle] + une règle de lifecycle optionnelle).
- **`base.html` / `style.css`** : `.sidebar` passe en `display:flex; flex-direction:column` avec un
  `.sidebar-spacer` (`flex:1`) inséré entre les liens Comptes/Synchronisation et le bloc
  Administration, qui se retrouve ainsi collé en bas de la fenêtre (juste au-dessus du pied de
  version) au lieu de s'empiler juste après Synchronisation.
- **Tests** (script client Flask + `moto`, non versionné, même méthode que v0.8.x/v0.9.0) : création
  combinant versionning+lock+lifecycle en un seul POST, création « plaine » sans aucune case, et
  `detect_canned_acl` sur bucket neuf (private), après `public-read`, et sur des grants personnalisés
  (WRITE seul sans READ ⇒ non détecté).

### Éditeur graphique de configuration de bucket — versionning, lock, lifecycle, policy, ACL (v0.9.0)

Nouveau bouton **Configurer** sur chaque bucket (droit `bucket_admin`, réutilisé — pas de nouveau
droit de groupe). Choix validés avec l'utilisateur avant implémentation : périmètre complet dès la
v1 (versionning + lock + lifecycle **et** policy + ACL, pas juste les 3 premiers) ; undo réel après
application (pas un simple rechargement de formulaire) — un seul niveau, l'état S3 capturé juste
avant le dernier « Enregistrer » de chaque section, remplacé à chaque nouvelle application.

- **`app/bucket_config.py`** (nouveau) : `get_*`/`set_*` symétriques par section — le get renvoie
  exactement ce que le set attend en entrée, ce qui permet à la route de faire l'undo générique
  (capturer `get_*` avant `set_*`, rejouer la valeur capturée à l'appel d'annulation) sans logique
  spécifique par section. Erreurs S3 « pas configuré » (`NoSuchLifecycleConfiguration`,
  `NoSuchBucketPolicy`, `ObjectLockConfigurationNotFoundError`) et « non supporté par ce provider »
  avalées et ramenées à un état par défaut plutôt que de faire planter la page.
  - Versionning : `Enabled`/`Suspended` seulement — irréversible vers « jamais activé » (limitation
    S3, pas un bug de l'app) ; l'undo depuis l'état « jamais configuré » est un no-op signalé par un
    message, la snapshot est quand même purgée.
  - Object Lock : seule la règle de rétention par défaut est éditable — `ObjectLockEnabled` est
    immuable après la création du bucket côté S3, donc affiché en lecture seule (page indique
    explicitement que ce n'est pas activable après coup).
  - Lifecycle : règles simplifiées (préfixe, activé, expiration objets, expiration versions
    précédentes, nettoyage multipart incomplet) ; liste vide ⇒ `delete_bucket_lifecycle` (attention,
    ce n'est **pas** `delete_bucket_lifecycle_configuration`, qui n'existe pas sur le client boto3).
  - Policy : éditeur JSON brut, validé (`json.loads`) côté serveur avant tout appel S3 ; policy vide
    ⇒ suppression plutôt que policy invalide.
  - ACL : formulaire = ACL prédéfinie (`private`/`public-read`/…) uniquement, pas d'édition de
    grants bruts (plus sûr) ; mais la snapshot d'undo capture les **grants exacts** (`get_bucket_acl`)
    et les rejoue via `put_bucket_acl(AccessControlPolicy=...)`, donc l'undo restaure fidèlement même
    une ACL d'origine non-« canned ». Bandeau d'alerte + confirmation JS avant toute ACL publique.
- **`storage.py`** : collection `bucket_config_snapshots`, une entrée par
  `(account_id, bucket, section)` — écrasée à chaque nouvel « Enregistrer », effacée une fois
  l'« Annuler » rejoué (pas d'historique multi-niveaux).
- **`app/routes/bucket_config_routes.py`** (nouveau blueprint) : une route POST par section
  (`/config/versioning`, `/lock`, `/lifecycle`, `/policy`, `/acl`) + une route générique
  `/config/<section>/undo`. Gate `bucket_admin` comme `bucket_new`/`bucket_delete`.
- **Template** `bucket_config.html` (onglets, un par section) + lien « Configurer » dans
  `buckets.html`. Icônes `settings`/`lock`/`undo`/`alert-triangle` ajoutées à `_macros.html`.
- **Tests** (script client Flask + `moto` mocké, non versionné, même méthode que la synchro) :
  cycle apply→undo pour les 5 sections, y compris le cas « versionning jamais activé » et un
  bucket créé avec Object Lock (`ObjectLockEnabledForBucket=True`) vs un bucket sans lock.

### Synchronisation — navigateur de préfixe/objet en liste (v0.8.2)

Même traitement que le bucket (v0.8.1) appliqué aux champs de chemin : la clé/préfixe source
(`sync_job_form.html`) et le préfixe destination (formulaire de job **et** bulk « Copier vers… »
de l'explorateur) proposent désormais un bouton **Parcourir…** ouvrant un petit navigateur en
ligne (fil d'Ariane + liste dossiers/objets du niveau courant) plutôt que de ne compter que sur la
saisie libre — celle-ci reste possible, le picker ne fait que pré-remplir le champ.

- **`GET /sync/browse?account_id=&perm=&bucket=&prefix=`** (JSON `{folders, objects}`) : un
  niveau à la fois (`Delimiter="/"`), même principe de portée bornée que la navigation de
  l'explorateur (pas de scan récursif). Droit revérifié côté serveur comme `/sync/buckets`.
- **`app/static/pathpicker.js`** (nouveau, vanilla) : widget déclaratif générique
  (`data-pathpicker` + `data-account-select` / `data-bucket-select` / `data-perm` /
  `data-allow-objects`), navigation dossier par dossier, clic sur un objet = sélection directe
  (uniquement proposé côté source, portée objet/préfixe), bouton « Utiliser ce dossier » pour
  fixer le préfixe courant. Se réinitialise si le compte ou le bucket change.
- CSS `.path-picker` / `.path-browser` / `.path-breadcrumb` / `.path-list`.

### Synchronisation — sélection du bucket en liste (v0.8.1)

Les champs bucket source/destination du formulaire de job (`sync_job_form.html`) et le
sélecteur de destination du bulk « Copier vers… » (`explorer.html`) étaient des champs texte
libres — remplacés par des `<select>` peuplés dynamiquement (fetch JS au changement de compte)
avec les buckets **réellement présents** sur le compte choisi, plutôt que de laisser taper un nom
à la main. Nouvelle route `GET /sync/buckets?account_id=&perm=download|upload` (JSON), qui
revérifie le droit (`download` côté source, `upload` côté destination) avant d'appeler
`list_buckets()` — même garde-fou que le reste de l'app, la liste affichée dans le `<select>` HTML
n'étant qu'un filtre côté client. En édition, le bucket déjà enregistré reste sélectionné (ou
apparaît marqué « introuvable » s'il a été supprimé entre-temps) plutôt que d'être perdu.

### Synchronisation planifiée entre buckets + copie inter-bucket — v0.8.0

Deux fonctionnalités livrées ensemble car elles partagent le même moteur : (1) planifier une
synchronisation entre deux buckets (objet / préfixe / bucket entier) et (2) copier un préfixe ou
des objets vers un autre bucket depuis l'explorateur. Choix validés avec l'utilisateur avant
implémentation :

- **Cross-compte / cross-provider** : source et destination peuvent être sur des comptes (et
  providers) différents. Pas de `CopyObject` S3 natif entre deux endpoints différents → les objets
  **transitent par le serveur** (`get_object` puis `upload_fileobj`, streamé, jamais bufferisé en
  RAM — contrairement au zip du téléchargement groupé existant).
- **Suppression à destination configurable par job** (case « supprimer les objets absents de la
  source », **décochée par défaut**) — pas un miroir strict imposé.
- **Pas admin-only** : tout utilisateur ayant `download` sur un compte et `upload` sur un autre
  (via ses groupes) peut créer ses propres jobs. Chaque user ne voit/gère que ses jobs sur
  `/sync` ; **Administration → Synchronisation** (`/admin/sync`) offre une vue globale
  (superviser, désactiver, reprendre à son nom) sans éditer le contenu du job d'un autre.
- **Droits revérifiés à chaque exécution planifiée**, pas seulement à la création : si l'owner a
  perdu `download` (source), `upload` (destination) ou `delete` (destination, si suppression
  activée), le job passe automatiquement `enabled=false` / `status.state="rights_error"` (visible
  dans les deux vues), sans notification (l'app n'a pas ce canal). Un admin le débloque en le
  « reprenant » à son nom (`reassign_sync_job` — un admin a tous les droits par construction, la
  reprise lève donc systématiquement le blocage) ; le propriétaire peut aussi le corriger en le
  modifiant (ré-active le job).
- **Copie manuelle (explorateur) = tâche de fond**, pas une requête HTTP bloquante : le bulk
  « Copier vers… » crée un job one-shot (`schedule.enabled=false`, portée `selection` = liste de
  clés déjà dépliée comme le zip existant) lancé dans un thread, suivi sur `/sync` (barre de
  progression, polling JSON toutes les 3 s). Un objet identique à destination (taille + ETag) est
  **ignoré**, pas réécrit.

Détails d'implémentation :

- **`storage.py`** : collection `sync_jobs` (`{id, owner_id, name, source{account_id, bucket,
  scope: object|prefix|bucket|selection, value, keys}, dest{account_id, bucket, prefix},
  delete_extraneous, schedule{enabled, frequency, weekday, hour, minute}, enabled, status{state,
  last_run_at, last_summary}, created_at}`). CRUD + `set_sync_job_enabled`, `reassign_sync_job`,
  `record_sync_job_result`. Un job édité par son propriétaire redevient `enabled=true` (efface un
  ancien `rights_error`).
- **`app/sync.py`** (nouveau, même esprit que `backup.py`) : moteur de transfert
  (`_resolve_keys` selon la portée, comparaison taille+ETag pour sauter les objets déjà à jour,
  `_delete_extraneous` par lot de 1000 comme le bulk delete existant, jamais d'exception non
  gérée) + planificateur généralisé à **N jobs** (un `threading.Timer` par job, au lieu du timer
  unique de `backup.py`) + verrou anti-chevauchement **par job**. Progression tenue **en mémoire
  uniquement** (`_progress`, jamais persistée — perdue au redémarrage comme le timer de backup,
  sans conséquence). **Mono-worker gunicorn requis** (comme la sauvegarde).
- **`auth.py`** : `accounts_with_permission(user, perm)` — comptes accessibles sur lesquels
  l'utilisateur tient un droit donné (peuple les `<select>` source/destination).
- **Routes** : `app/routes/sync_routes.py` (nouveau blueprint `/sync` — dashboard scoped owner,
  CRUD job, `/run`, `/status` JSON pour le polling). `admin_routes.py` : `/admin/sync` (+
  `/toggle`, `/reassign`). `explorer_routes.py` : `object_bulk` gagne l'action `copy` (compte /
  bucket / préfixe destination saisis dans la barre d'actions groupées existante).
- **Templates** : `sync_jobs.html` (dashboard + badges d'état + barre de progression + polling
  JS), `sync_job_form.html` (création/édition, JS bascule portée/planification comme
  `admin_backup.html`), `admin_sync.html` (vue globale). `explorer.html` : bloc « Copier vers… »
  dans la barre bulk existante. Icônes `repeat` / `copy` ajoutées à `_macros.html`. CSS
  `.sync-progress(-bar)`.
- Un job né d'une copie manuelle (portée `selection`, liste de clés figée) n'est **pas éditable**
  (`job_edit` redirige avec un message) — seulement lançable/supprimable.
- **Tests** (script client Flask + `moto` mocké, non versionné) : sync par préfixe (copie,
  idempotence sur ré-exécution, miroir avec suppression après ajout/retrait côté source), copie
  manuelle bulk depuis l'explorateur (job `selection` créé + exécuté en tâche de fond, contenu
  vérifié), rendu des pages (dashboard, formulaire, admin), perte de droits → désactivation
  automatique, reprise admin → déblocage + ré-exécution OK, arithmétique du planificateur
  (quotidien → lendemain 03:00 UTC).

### Diffusion par image GHCR + mise à jour en mode conteneur

Choix validés avec l'utilisateur : cible = **auto-hébergeurs externes** (image publique GHCR comme
canal principal) ; application de la MAJ = **bouton in-app** ; déclencheur = **tag git `v*`** ;
plateforme **`linux/amd64`** ; accès au socket Docker via un **sidecar `updater`** dédié (pas dans
l'app — cf. `SECURITE.md`, l'app stocke des SK chiffrées).

- **`.github/workflows/release.yml`** (nouveau, seul workflow) : sur `push` de tag `v*`, vérifie que
  `v$(cat VERSION)` == tag, construit et pousse `ghcr.io/glorp-fr/s3-multi-browser` **et**
  `…-updater` (tags `vX.Y.Z`, `X.Y`, `latest`) via `docker/metadata-action` + `build-push-action`
  (`GITHUB_TOKEN`, pas de PAT), puis crée une **GitHub Release** (notes auto). `Dockerfile` : label
  `org.opencontainers.image.source`.
- **`docker-compose.yml`** repensé pour la prod : `image:` GHCR au lieu de `build:`, service
  `updater` (socket monté, **aucun port publié**, réseau `updater_net` `internal: true`), réseau
  `frontend` séparé pour l'app (sortie internet + S3). **`docker-compose.override.yml`** (nouveau)
  restaure `build: .` en dev et met `updater` derrière un profil. **`.env.example`** (nouveau) avec
  `UPDATE_TOKEN`.
- **`updater/`** (nouveau, image séparée `docker:27-cli` + `python3`) : `updater.py`, `http.server`
  stdlib, `POST /update` protégé par `Authorization: Bearer $UPDATE_TOKEN`
  (`hmac.compare_digest`), exécute **uniquement** `docker compose -f docker-compose.yml pull` puis
  `up -d` dans `/work` (compose monté en RO). Rien du corps de requête n'est utilisé. `GET /healthz`.
  Verrou anti-chevauchement, erreurs `OSError`/timeout renvoyées en JSON, refuse de démarrer sans
  token.
- **`app/version.py`** : détection du mode au runtime. Mode **git** inchangé. Mode **image**
  (`not is_git`) : `_check_image()` lit `GET /repos/{REPO}/releases/latest` et compare le `tag_name`
  au `VERSION` local (`_semver()`, tuple, tolère `v`, `-rc`, `+build`) ; `_apply_image()` fait un
  `POST {UPDATER_URL}/update` avec le jeton, ou renvoie la commande manuelle si `UPDATE_TOKEN` est
  vide / le sidecar injoignable. Cache `version_check.json` enrichi (`mode`, `latest_version`,
  `release_url`) ; `update_available()` gère les deux modes (rétro-compatible avec l'ancien cache).
- **`admin_routes.py`** : `version_page` passe `mode` + `updater_ready` au template ; `version_check`
  et `version_update` ne présument plus le mode git (plus de `KeyError` sur `behind_by` / `old` /
  `new`). **`admin_version.html`** : branché sur `mode`, bouton *Mettre à jour vers vX.Y.Z* en mode
  image si `updater_ready`, sinon encart avec la commande manuelle ; carte et pied de page adaptés.
- **README** : section « Lancer avec Docker » réécrite (pull GHCR, `docker compose`, sidecar +
  **encadré sécurité** sur le socket, procédure de publication par tag) ; table des variables
  d'env complétée (`UPDATE_TOKEN`, `UPDATER_URL`, `UPDATE_REPO` étendu aux releases).
- **Tests** (scripts client Flask, non versionnés) : `_semver`, `check_update` mode image
  (retard / à jour / 404 sans release), `apply_update` mode image (sans token → commande manuelle ;
  sidecar OK ; sidecar renvoie un échec ; sidecar injoignable), rendu `admin_version.html` dans les
  deux modes (badge conteneur, bouton visible seulement avec token), POST `/admin/version/*` sans
  500. Mode git : page rendue, régression OK. Sidecar : `healthz`, 403 sans/mauvais token, erreur
  gracieuse si `docker` absent.

**Reste à faire manuellement après le 1er tag** : rendre le package GHCR **public**
(Settings du package sur GitHub) pour le pull anonyme.

### Application de SECURITE.md — Module « Sauvegarde de configuration » — v0.7.x

Livré depuis un **clone séparé** poussé sur GitHub, sans toucher au gunicorn en place :
le but était de valider de bout en bout la détection de MAJ + le bouton *Mettre à jour* de
`Administration → Version`.

Choix validés avec l'utilisateur : destinations **S3 et SMB toutes deux fonctionnelles** ;
contenu = `db.json` **+** `audit.jsonl` (+ `.1`) ; planification par **thread interne** au
process (pas de cron).

**Correctif `version.py` (détection de MAJ)** : `check_update()` faisait
`GET /compare/<local>...<branche>` mais lisait `behind_by`, toujours ≈ 0 dans ce sens (GitHub
décrit le *head* par rapport au *base*). Du coup l'instance se croyait toujours « à jour » et,
`behind_by` valant 0, le bouton *Mettre à jour* (affiché seulement si `behind_by > 0`) ne
s'affichait jamais. Corrigé : on lit `ahead_by` (commits de la branche absents du checkout =
retard) et `behind_by` devient le compte des commits locaux non poussés (cas divergé).

**Conséquence de bootstrap** : l'instance qui tournait (v0.6.0 / `7030b67`) avait ce bug — elle
ne pouvait pas se mettre à jour elle-même via l'UI. Les commits `1f8d0cd` (module sauvegarde) +
`18a788b` (le correctif) ont donc été appliqués **une fois à la main** sur le serveur, avec
exactement les commandes du bouton : `git fetch --prune origin` puis
`git merge --ff-only origin/master` (fast-forward propre `7030b67` → `18a788b`) puis
`kill -HUP <master gunicorn>`. À partir de `18a788b` la détection fonctionne et le cycle
*Vérifier → Mettre à jour → reload* se valide depuis l'UI au push suivant.

- **Nouvelle dép.** : `smbprotocol==1.17.0` (client SMB2/3 pur Python, dans le venv — pas de
  `smbclient` système). Ajoutée à `requirements.txt` **et** installée à la main dans le venv de
  l'instance en place (sinon la page planterait après la MAJ, `git merge` ne faisant pas de
  `pip install`).
- **`storage.py`** : bloc `backup` dans `db.json` (`_default_backup()` + `_merge_defaults()` pour
  compléter les configs anciennes). `get_backup_config()`, `update_backup_config(...)`
  (validation horaire/fréquence/rétention ; secrets `smb.password` / `s3.secret_key` chiffrés
  Fernet dans `*_enc`, laissés vides = inchangés), `backup_smb_password()` /
  `backup_s3_secret_key()`, `record_backup_result()`.
- **`app/backup.py`** (nouveau) : `_build_archive()` → `.tar.gz` en mémoire de
  `data/{db.json,audit.jsonl,audit.jsonl.1}` nommé `config-backup-YYYYmmdd-HHMMSS.tar.gz`.
  `_push_s3()` (boto3 `put_object` + `list_objects_v2` + `delete_objects` pour la rétention) ;
  `_push_smb()` (`smbclient.register_session` / `mkdir` récursif / `open_file` / `listdir` /
  `remove`). `run_backup()` **ne lève jamais** (retourne `{ok, message}`, journalise
  `admin/backup_run`, verrou non bloquant anti-chevauchement). Planificateur :
  `_next_run_at()` (quotidien/hebdo, UTC), `reschedule()` (annule puis ré-arme un
  `threading.Timer` daemon selon la config ; no-op si désactivé), `start()` appelé depuis
  `create_app()`. **Mono-worker gunicorn requis.**
- **`admin_routes.py`** : `GET/POST /admin/backup` (form) + `POST /admin/backup/run`
  (synchrone, acteur = admin courant). Audit `admin/backup_config` + `admin/backup_run`.
- **Templates** : `admin_backup.html` (nouveau : activer, radio destination S3/SMB avec
  bascule JS des fieldsets, params S3, params SMB, fréquence + jour (si hebdo) + heure/minute
  UTC + rétention, bouton *Sauvegarder maintenant*, encart statut dernière/prochaine exéc.).
  `base.html` : lien sidebar « Sauvegarde » (icône `save` ajoutée à `_macros.html`).
- **Tests** (script client Flask, DATA_DIR isolé) : rendu page, `_build_archive` (membres
  `db.json`+`audit.jsonl`), POST config S3 (persistance, chiffrement SK, SK vide = conservée),
  `_next_run_at` quotidien/hebdo, minuteur armé si activé / annulé si désactivé, `run_backup`
  sur endpoint invalide → échec gracieux enregistré (`last_status=fail`), SMB non configuré →
  message « incomplète ». Régression : smoke groups v0.6.0 rejoué OK.

### Application de SECURITE.md — Gestion des groupes (RBAC) — v0.6.0

Remplacement du modèle d'autorisation « rôle global + liste de comptes » par un modèle
**groupes**. Choix validés avec l'utilisateur avant implémentation :

- **Un groupe = unité de contrôle d'accès porteuse des droits.** `user → groupe(s) → comptes S3
  + droits`. Pas de « groupe de comptes » séparé.
- **Droits fins portés par le groupe** : `download`, `upload`, `delete`, `bucket_admin`
  (créer/supprimer un bucket). `list`/navigation (lister les buckets, parcourir, rechercher,
  recalculer la volumétrie) est **implicite** dès qu'un groupe couvre le compte — pas de case à
  cocher. Un groupe sans droit = lecture seule.
- **Rôle global supprimé** : `operateur` / `readonly` disparaissent. Il reste un flag
  `is_admin` (super-admin : accès total + pages d'administration, groupes ignorés). Tout accès
  d'un non-admin passe par ses groupes.
- **Multi-appartenance** : un utilisateur peut être dans plusieurs groupes ; accès et droits
  effectifs = **union** des groupes.
- **Portée des comptes d'un groupe** : liste explicite **ou** case « tous les comptes »
  (équivalent de l'ancien `*`, inclut les comptes futurs).
- **Migration** (`storage.migrate()`, idempotente) : chaque user perd `role` (→ `is_admin =
  role == "admin"`) et `account_ids` (supprimé, **pas** de reconstruction auto en groupe — décidé
  ainsi, les 2 users existants étaient admin), gagne `group_ids: []`. Sauvegarde de `data/db.json`
  faite avant test dans `data/db.json.bak-pre-groups`.

Détails d'implémentation :

- **`storage.py`** : `GROUP_PERMISSIONS`, collection `groups` dans `db.json`
  (`{id, name, permissions[], all_accounts, account_ids[]}`), CRUD `create/update/delete_group`
  (nom unique ; `delete_group` **bloqué** si des utilisateurs y sont encore, comme
  `delete_provider`). `create_user` / `update_user` : signature `(…, is_admin, group_ids)`.
  Garde-fou **dernier admin** (`_last_admin_guard`) sur `update_user` (décoche admin) et
  `delete_user`. `delete_account` retire désormais le compte des `account_ids` des groupes.
- **`auth.py`** : `is_admin()`, `admin_required` (remplace `role_required("admin")`),
  `account_permissions(user, account_id)` (set de droits, union des groupes ; admin = tous),
  `can(user, account_id, perm)`. `accessible_accounts` / `can_access_account` réécrits sur les
  groupes. `ROLES`, `role_required`, `can_write` supprimés.
- **`explorer_routes.py`** : chaque garde `can_write(g.user)` → `can(g.user, account_id, <perm>)`
  avec la perm précise (`bucket_admin` pour create/delete bucket, `upload` pour upload/mkdir,
  `delete` pour delete-object(s), `download` pour download simple **et** zip groupé — le
  téléchargement était auparavant non gardé). `bucket_usage_refresh` n'est plus gardé en écriture
  (opération de lecture). Les vues passent `perms=account_permissions(...)` au lieu de `can_write`.
- **`admin_routes.py`** : section Groupes (`/admin/groups`, `/new`, `/<id>/edit`, `/<id>/delete`),
  audit `admin` / `group_create|group_update|group_delete`. Formulaire user : `is_admin`
  (checkbox) + `group_ids` (checkboxes), plus de `role` ni de sélecteur de comptes.
- **Templates** : `admin_groups.html` + `admin_group_form.html` (nouveaux) ; `admin_user_form.html`
  et `admin_users.html` réécrits (type admin/utilisateur + liste des groupes) ; `base.html` lien
  sidebar « Groupes » (icône `users` ajoutée à `_macros.html`) et badge `is_admin` ;
  `buckets.html` / `explorer.html` : `{% if can_write %}` → `{% if '<perm>' in perms %}`, le
  bouton et le lien de téléchargement sont masqués sans `download`. `style.css` : `.bdg.user`.
- **Tests** (script client Flask non versionné, DB isolée) : migration (admins préservés, clés
  legacy retirées), CRUD groupe + assainissement `all_accounts`/`account_ids`, création user dans
  un groupe, garde-fou dernier admin, `delete_group` bloqué avec membre, session non-admin
  (list implicite OK, 404 sur compte non accordé, 403 sur create/delete bucket + upload +
  delete-object, 403 sur les pages admin), rendu des 4 pages admin (liste + formulaires).

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

### Application de SECURITE.md — Module « Logs »

`SECURITE.md` (à la racine de `~/claude`) décrit un socle de modules attendus pour toute app.
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
    défilement » de SECURITE.md). Rendu des lignes JS en `textContent` (pas d'injection HTML depuis
    le contenu des logs).
  - Onglet **« Historique des connexions »** : table server-rendered des événements `auth`
    (date, utilisateur, IP, résultat, événement, user-agent) avec son propre champ de filtre
    (`?q=`, rechargement page, `?tab=conn` pour rouvrir l'onglet).
- **Icônes** ajoutées à `_macros.html` : `activity`, `pause`.
- **Tests** (scripts client de test Flask + S3 mocké, non versionnés) : écriture/lecture/curseur
  `audit`, login OK/KO + logout journalisés, page `/admin/logs` + endpoint `tail` (filtres `q` et
  `cat`), CRUD admin audité, et instrumentation S3 (buckets/objects/download/create/mkdir/upload/
  delete objet+dossier/delete bucket) — plus vérif qu'aucun secret ne fuit dans les logs.

### Actions groupées (multi-sélection) dans l'explorateur

Choix validés : les 4 actions groupées demandées, motif « cases à cocher + boutons d'action
au-dessus du tableau » (désactivés tant que rien n'est coché).

- **`app/static/bulk.js`** (vanilla, ~50 l, pas de dépendance) : câble tout `<form data-bulk>` —
  case d'en-tête `.bulk-all` (avec état indeterminate), cases `.bulk-item` (leur `name`/`value`
  portent la charge utile), boutons `[data-bulk-action]` activés/désactivés selon la sélection,
  `.bulk-count` (compteur), confirmation via `data-bulk-confirm="… %n …"` sur le bouton concerné.
  Chargé par un nouveau bloc `{% block scripts %}` dans `base.html`.
- **Contrainte HTML** : pas de `<form>` imbriqués. Les formulaires d'action par ligne (refresh /
  delete unitaires) de `buckets.html` et `explorer.html` ont donc été **retirés** au profit de la
  sélection ; le lien de téléchargement unitaire (`<a>`) reste, valide dans un `<form>`.
- **`bucket_bulk`** (`POST /accounts/<id>/buckets/bulk`, champ `bucket` multiple + `action`) :
  - `usage_refresh` : recalcule la volumétrie des buckets cochés, **saute** ceux dont le dernier
    calcul date de moins de 24 h (`usage_cache.is_refresh_allowed`) ; résumé `done / skipped /
    errors` en flash + audit `s3_read/usage_refresh_bulk`.
  - `delete` : `delete_bucket` sur chaque bucket coché (`can_write` requis, sinon 403) ;
    audit `s3_write/delete_bucket_bulk`.
- **`object_bulk`** (`POST /accounts/<id>/buckets/<bucket>/bulk`, champ `key` multiple — objets
  **et** préfixes « dossier » finissant par `/` — + `prefix` courant + `action`) :
  - `delete` : `_expand_keys()` déplie les dossiers en objets réels, puis `delete_objects` par lots
    de 1000 ; comptes `Deleted`/`Errors` ; `can_write` requis ; audit `s3_write/delete_object_bulk`.
  - `download` : déplie la sélection, construit un **`.zip`** en mémoire (`zipfile.ZIP_DEFLATED`,
    `BytesIO`), `arcname` relatif au préfixe courant, renvoyé en `send_file` (`application/zip`,
    nom = dernier segment du préfixe ou bucket). **Pas** de `can_write` (lecture). Audit
    `s3_read/download_zip`. Coûteux sur gros volumes (tout en RAM) — l'utilisateur en a été averti.
- **CSS** : `.bulk-bar`, colonne `.chk` (36 px) dans `style.css`.
- **Tests** (`test_bulk.py`, non versionné, S3 mocké) : refresh groupé + règle des 24 h, delete
  groupé de buckets, delete groupé objets (dossier déplié récursivement), download `.zip` (membres
  + contenu vérifiés via `zipfile`), 403 pour `readonly` sur les deletes, `readonly` autorisé au
  `.zip`, sélection vide → erreur. Rendu des templates `buckets.html` / `explorer.html` re-vérifié.

### Application de SECURITE.md — Module « Version & mises à jour »

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
  utilisateurs** (exigence page user de SECURITE.md). Icône `tag` ajoutée à `_macros.html`.
- **`Dockerfile`** : `COPY VERSION .`. En conteneur il n'y a pas de `.git` → `apply_update()`
  renvoie le message « instance non gérée par git » (comportement attendu ; MAJ = rebuild d'image).
- **Contexte Jinja** : `app/__init__.py` injecte `app_version` et `update_available` dans tous les
  templates.
- **Tests** (script non versionné) : lecture `VERSION`, `git_info`, `check_update` (en retard / à
  jour / HTTPError 404), garde-fous `apply_update` (arbre sale, pas de git, ff impossible), happy
  path avec git mocké (enchaînement fetch → merge --ff-only), routes admin + audit, version visible
  en sidebar pour un non-admin.

**Reste à appliquer de SECURITE.md** (dans l'ordre convenu) : couche **groupes** (un groupe porte
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
- **Charte graphique** : CSS transcrit depuis `~/claude/CHARTE_GRAPHIQUE.md` (variables couleur, polices
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
  sync.py                 # moteur de transfert + planificateur des jobs de synchro/copie inter-bucket
  routes/
    auth_routes.py       # /login /logout  (+ audit auth)
    admin_routes.py       # /admin/providers, /admin/accounts, /admin/users (CRUD, audités)
                          #   + /admin/logs (+ /logs/tail), /admin/version (+ /version/check, /version/update)
                          #   + /admin/sync (vue globale des jobs : toggle, reassign)
    explorer_routes.py     # /accounts, buckets, objets, upload/download/delete, usage/refresh (audités)
                          #   + bucket_bulk / object_bulk (actions groupées : refresh, delete, zip, copy)
    sync_routes.py          # /sync (dashboard scoped owner, CRUD job, run, status JSON pour polling)
  templates/             # Jinja2, un template par page + _macros.html (icônes SVG inline)
                          #   admin_logs.html (journal live + connexions), admin_version.html
                          #   sync_jobs.html / sync_job_form.html / admin_sync.html
  static/style.css        # CSS transcrit de ~/claude/CHARTE_GRAPHIQUE.md (+ styles .log-view / .tabs / .ver-* / .bulk-bar / .sync-progress)
  static/bulk.js          # multi-sélection des tableaux <form data-bulk> (vanilla)
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
