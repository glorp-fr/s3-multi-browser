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








