#!/usr/bin/env bash
# Installe ffmpeg sur un runner GitHub (Ubuntu) sans pouvoir bloquer le passage.
#
# Le miroir Ubuntu du runner (Azure) reste parfois bloqué ou très lent : 12 min sans rien afficher au passage
# d'essai n° 108 (06/11), trois essais de 4 min coupés aux tests de la PR n° 50. Chaque essai est donc borné, une
# connexion muette est coupée au bout de 30 s, et après un premier échec on passe par l'archive officielle d'Ubuntu
# (mêmes paquets, mêmes signatures). L'étape qui l'appelle a son propre temps limite (timeout-minutes).
set -u

opts=(-qq -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=2)
for attempt in 1 2 3; do
  if timeout -k 10 90 sudo apt-get update "${opts[@]}" &&
     timeout -k 10 180 sudo apt-get install -y "${opts[@]}" ffmpeg; then
    exit 0
  fi
  echo "::warning::Installation de ffmpeg bloquée ou en échec (essai $attempt sur 3)"
  sudo dpkg --configure -a || true
  if [ "$attempt" = 1 ]; then
    echo "Miroir du runner trop lent : passage par archive.ubuntu.com."
    sudo sed -i 's|http://azure.archive.ubuntu.com/ubuntu|http://archive.ubuntu.com/ubuntu|g' \
      /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list 2>/dev/null || true
  fi
  sleep 10
done
exit 1
