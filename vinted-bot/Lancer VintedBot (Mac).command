#!/bin/bash
# Double-cliquez sur ce fichier pour lancer VintedBot sur Mac.
# (Au premier lancement : clic droit > Ouvrir, si macOS bloque le fichier.)
cd "$(dirname "$0")" || exit 1
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo
  echo "Python 3.11 ou plus récent est nécessaire."
  echo "Téléchargez-le sur https://www.python.org/downloads/ puis relancez ce fichier."
  read -r -p "Appuyez sur Entrée pour fermer…"
  exit 1
fi
"$PY" -m vintedbot setup --if-needed || { read -r -p "Appuyez sur Entrée pour fermer…"; exit 1; }
echo
echo "VintedBot démarre. Laissez cette fenêtre ouverte (Ctrl+C pour arrêter)."
echo
"$PY" -m vintedbot run
read -r -p "Appuyez sur Entrée pour fermer…"
