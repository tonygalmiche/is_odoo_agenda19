#!/bin/bash

# Récupère uniquement le module web_m2x_options depuis le dépôt OCA/web
# branche 19.0, sans télécharger les autres modules.
#
# Options du clone :
#   --depth 1          : Ne récupère que le dernier commit (pas l'historique)
#   --branch 19.0      : Cible uniquement la branche 19.0
#   --filter=blob:none : Ne télécharge pas le contenu des fichiers lors du clone (lazy loading)
#   --sparse           : Active le mode sparse checkout (dossiers sélectifs)
git clone --depth 1 --branch 19.0 --filter=blob:none --sparse https://github.com/OCA/web.git /tmp/web

cd /tmp/web

# Après le clone, Git n'a aucun fichier dans le working directory.
# Cette commande indique à Git de ne matérialiser que le dossier web_m2x_options.
# Tous les autres dossiers du dépôt sont ignorés.
git sparse-checkout set web_m2x_options
