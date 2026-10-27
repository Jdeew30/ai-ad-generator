# Clipper : la chaîne de clips automatisée (Twitch / YouTube → YouTube Shorts, TikTok)

Un robot qui, plusieurs fois par jour :

1. **repère** les meilleurs moments des gros streamers : les clips Twitch les plus vus des dernières heures
   (ce sont les viewers eux-mêmes qui clippent les moments drôles), les pics « les plus revus » des vidéos
   YouTube, et les URLs que tu ajoutes à la main ;
2. **trie** : anti-doublon (le même moment clippé par 10 viewers ne sort qu'une fois), jamais deux fois le
   même clip, 2 clips max par streamer et par jour, popularité *relative* au streamer, fraîcheur ;
3. **fait juger** les candidats par **Claude** (facultatif) : transcription + 4 images de chaque extrait, il
   écarte ce qui est risqué (insultes, contenu sexuel, infos privées, musique protégée…), note le potentiel
   viral, coupe les temps morts et écrit l'accroche, le titre, la description et les hashtags ;
4. **monte** chaque short en 1080×1920 : facecam en haut et jeu en bas (détection automatique du visage),
   ou plein cadre centré sur le streamer, ou fond flouté ; **sous-titres animés mot à mot** (Whisper),
   accroche incrustée, crédit `twitch.tv/<streamer>`, son normalisé ;
5. **publie** sur YouTube Shorts et TikTok, et t'envoie chaque vidéo sur **Telegram** avec le texte prêt
   à coller ;
6. chaque dimanche, monte un **best-of de la semaine** en 16:9 (compte à rebours #12 → #1, bandeau avec le
   nom du streamer, carte d'intro, miniature générée, chapitres et crédits dans la description).

Tout tourne gratuitement dans **GitHub Actions** (aucun serveur à louer). Le robot se souvient de ce qu'il
a déjà publié grâce à une branche dédiée `clipper-state`.

## État réel (à lire avant de compter dessus)

| Fonction | État | Preuve / limite |
| --- | --- | --- |
| Montage vertical (3 mises en page, sous-titres, accroche, crédit) | ✅ Vérifié | Vrai ffmpeg sur des vidéos de synthèse avec un vrai visage : dimensions, durée, son, mot surligné présent à l'image, visage retrouvé dans le panneau facecam. Images contrôlées à l'œil. |
| Détection facecam / plein cadre / sans visage | ✅ Vérifié, y compris en réel | YuNet (OpenCV) sur une vraie photo incrustée en facecam ou en plein cadre. Sur de vrais clips d'AmineMaTue : caméra plein cadre reconnue (visage sur 8 images sur 8 → cadrage centré sur lui) ; sur un clip de réaction, visages vus sur 5 images sur 8 mais à des endroits différents (les personnes de la vidéo regardée) → pas de facecam stable → « fond flouté », volontairement. Une facecam très petite ou de profil peut ne pas être détectée : on peut alors donner sa position exacte par streamer. |
| Best-of 16:9, miniature, chapitres YouTube | ✅ Vérifié | Vrai ffmpeg + Pillow ; règles des chapitres (0:00, 3 minimum, 10 s minimum) testées. |
| Anti-doublons, quotas par streamer, mémoire entre passages | ✅ Vérifié | Tests de bout en bout sur 4 passages successifs, et en réel : sur 61 clips du moment, doublons d'un même moment et quota par streamer écartés. |
| Découverte Twitch sans clés (yt-dlp) | ✅ Vérifié en réel | Job `smoke` de **Clipper CI** (sur les serveurs de GitHub) : 61 clips trouvés sur les 24 chaînes, les meilleurs téléchargés, transcrits et montés en shorts (artefact du workflow). |
| API Twitch officielle (avec clés) | ⚠️ Écrite, testée contre un faux serveur | Forme des requêtes conforme à la doc Helix (Get Clips, Get Users, Get Streams, Get Games). Jamais exécutée avec de vraies clés. |
| Sous-titres Whisper | ✅ Vérifié en réel | Modèle `small` sur de vrais clips en français : « Il a défoncé la main avec le crayon ! Il lui a planté la main ? Ah oui ! » ; « …mesdames et messieurs, veuillez accueillir ! ». Des erreurs restent possibles sur l'argot, les noms propres ou un fond musical fort. |
| Revue et textes par Claude | ⚠️ Écrit, testé avec le vrai SDK contre un faux serveur | Forme exacte de la requête vérifiée (sortie JSON structurée, `effort`, repli serveur en cas de refus). Jamais appelé avec une vraie clé. Sans clé : gabarit non-IA (titre du clip d'origine). |
| Publication YouTube | ⚠️ Écrite, testée contre un faux serveur | Envoi « resumable » par morceaux avec reprise après coupure. **Tant que ton projet Google n'a pas passé l'audit de l'API, YouTube verrouille les vidéos envoyées en privé** (voir plus bas). |
| Publication TikTok | ⚠️ Écrite, testée contre un faux serveur | Brouillon (« inbox ») ou publication directe. **TikTok doit d'abord valider ton app**, et la publication directe publique exige un audit. |
| Envoi Telegram | ⚠️ Écrit, testé contre un faux serveur | Le plus simple à brancher (2 minutes), et utilisable tout de suite. |
| Workflows GitHub Actions | ⚠️ À valider sur ton dépôt | Les tests tournent à chaque push. Le planning (3 shorts par jour + best-of du dimanche) ne démarre qu'une fois le workflow sur la branche par défaut. |

**Le premier passage avec tes vrais comptes est la vraie validation des publications.** Toute erreur
(quota, jeton expiré, refus de la plateforme) apparaît telle quelle dans le résumé du workflow et sur
Telegram ; rien n'est marqué « publié » sans réponse positive de la plateforme.

## Démarrage rapide

### Sur GitHub (recommandé : 100 % automatique)

1. Fusionne cette branche dans la branche par défaut du dépôt (le planning ne tourne que depuis celle-ci).
2. Va dans **Actions → Clipper → Run workflow**, coche **Essai à blanc** et lance. Au bout de quelques
   minutes, le short monté est téléchargeable dans les **Artifacts** du run. Sans aucun identifiant, le
   robot fonctionne déjà : clips Twitch via yt-dlp, textes par gabarit, vidéos en artefacts.
3. Ajoute les secrets au fur et à mesure (**Settings → Secrets and variables → Actions → New repository
   secret**), dans l'ordre conseillé : Telegram, Claude, Twitch, puis YouTube et TikTok (détails plus bas).
4. C'est tout : 3 shorts par jour (vers 12 h, 18 h et 20 h 30 à Paris l'hiver, une heure plus tard l'été)
   et le best-of le dimanche matin.

Le workflow sert aussi de télécommande depuis l'appli GitHub sur ton téléphone : **Run workflow** →
mode `url` → colle l'URL d'un clip ou d'une vidéo (avec début et fin si besoin) → il est monté et publié.

### Sur ton ordinateur (pour tester ou pour YouTube)

Prérequis : Python 3.10+ et ffmpeg (`winget install ffmpeg`, `brew install ffmpeg` ou `apt install ffmpeg`).

```bash
cd clipper
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # puis remplis ce que tu as
python -m clipper doctor                            # vérifie l'installation
python -m clipper discover                          # liste les meilleurs clips du moment, sans rien télécharger
python -m clipper run --dry-run                     # monte 1 short dans output/ sans publier
python -m clipper render https://clips.twitch.tv/XXXX   # monte un clip précis
python -m clipper compile --dry-run                 # monte le best-of de la semaine
```

Chaque vidéo est accompagnée d'un `.txt` (titre, description YouTube, légende TikTok prêts à coller) et
d'un `.json`.

## Brancher les comptes (une seule fois)

Tous les identifiants sont facultatifs : chacun débloque une fonction. Ils se mettent en **secrets GitHub**
(et/ou dans `clipper/.env` pour un lancement local). Ne les colle jamais dans un fichier commité.

### Telegram : recevoir les vidéos sur ton téléphone (2 min)

1. Dans Telegram, écris à **@BotFather** → `/newbot` → note le jeton (`TELEGRAM_BOT_TOKEN`).
2. Écris `/start` à ton nouveau bot.
3. `TELEGRAM_BOT_TOKEN=… python -m clipper auth telegram` affiche ton `TELEGRAM_CHAT_ID`.

C'est le meilleur moyen de démarrer TikTok pendant que ton app TikTok est en validation : tu reçois la
vidéo + la légende, tu enregistres, tu publies (ou tu programmes dans TikTok Studio).

### Claude : le tri et les textes par IA

Crée une clé sur [platform.claude.com](https://platform.claude.com) → secret `ANTHROPIC_API_KEY`.
Modèle par défaut : `claude-opus-5-5`, effort `medium`, avec le repli automatique côté serveur si une
demande est déclinée. Une seule requête par passage pour tous les candidats.

### Twitch : la découverte des lives (5 min, gratuit)

Sans clés, le robot lit les clips des streamers de `config.yaml` via yt-dlp. Avec les clés, il utilise
l'API officielle, **ajoute automatiquement les plus gros lives FR du moment**, et connaît la position de
chaque clip dans la VOD (anti-doublon plus précis).

1. Active la double authentification sur ton compte Twitch.
2. [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) → **Register Your Application** →
   URL de redirection `http://localhost`, type « Confidential ».
3. Secrets `TWITCH_CLIENT_ID` et `TWITCH_CLIENT_SECRET` (bouton « New Secret »).

### YouTube : publication automatique

1. [console.cloud.google.com](https://console.cloud.google.com) → crée un projet → **API et services →
   Bibliothèque** → active **YouTube Data API v3**.
2. **Écran de consentement OAuth** : type « Externe », ton e-mail, puis **publie l'application (« En
   production »)**. En mode « Test », Google invalide le jeton au bout de 7 jours et le robot s'arrête.
   Pour un usage perso, pas besoin de faire vérifier l'app : à la connexion, clique « Paramètres avancés →
   Accéder à … ».
3. **Identifiants → Créer un ID client OAuth → Application de bureau** → `YOUTUBE_CLIENT_ID` et
   `YOUTUBE_CLIENT_SECRET`.
4. Sur ton ordinateur : `python -m clipper auth youtube` → connecte le compte **de la chaîne** → copie le
   `YOUTUBE_REFRESH_TOKEN` affiché en secret.
5. ⚠️ **Audit obligatoire pour publier en public** : les vidéos envoyées par un projet non audité sont
   verrouillées en privé. Remplis le formulaire
   [YouTube API Services – Audit and Quota Extension](https://support.google.com/youtube/contact/yt_api_form)
   (gratuit, quelques jours à quelques semaines). En attendant, laisse `publish.youtube.enabled: false` et
   publie à la main depuis Telegram ou les artefacts.
6. Quota : 10 000 unités par jour par défaut. La grille de Google compte 1 600 unités par envoi (vérifie
   le chiffre actuel dans la console), soit environ 6 envois par jour : assez pour 3 shorts + le best-of.
   Les miniatures personnalisées exigent une chaîne validée par téléphone
   ([youtube.com/verify](https://www.youtube.com/verify)).

### TikTok : envoi automatique

1. [developers.tiktok.com](https://developers.tiktok.com) → crée une app (il faut une page web avec
   conditions d'utilisation et politique de confidentialité : une page GitHub Pages suffit).
2. Ajoute **Login Kit** et **Content Posting API**, les scopes `user.info.basic`, `video.upload`,
   `video.publish`, et une URL de redirection HTTPS (par exemple ta page GitHub Pages).
3. Soumets l'app à la validation de TikTok. En attendant, le mode *sandbox* ne publie qu'en privé.
4. Sur ton ordinateur : `python -m clipper auth tiktok --redirect-uri https://…` → ouvre le lien, accepte,
   puis colle l'adresse de la page d'arrivée → `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`,
   `TIKTOK_REFRESH_TOKEN` en secrets.
5. Mode **`inbox`** (défaut) : la vidéo arrive en brouillon dans ton appli TikTok, tu ajoutes un son
   tendance si tu veux, et tu publies d'un tap. Mode **`direct`** : publication sans toi, mais TikTok
   n'autorise le public qu'aux apps **auditées** (sinon erreur `unaudited_client_can_only_post_to_private_accounts`,
   que le robot explique).
6. TikTok peut renouveler le refresh token. Pour que le robot l'enregistre seul, ajoute le secret
   `GH_SECRETS_TOKEN` : un jeton GitHub *fine-grained* limité à ce dépôt avec la permission
   « Secrets : Read and write ». Sans lui, le journal te prévient s'il faut mettre le secret à jour.

### YouTube comme source (facultatif)

`youtube.enabled: true` et des chaînes dans `youtube.channels` : le robot prend les vidéos récentes et
découpe les pics « les plus revus ». YouTube bloque souvent les serveurs de GitHub (« Sign in to confirm
you're not a bot ») : dans ce cas, lance cette partie depuis ton ordinateur, ou fournis des cookies
(`YTDLP_COOKIES`, format Netscape, idéalement d'un compte secondaire).

## Réglages utiles (`config.yaml`)

- `twitch.streamers` : la liste des logins suivis (24 gros streamers FR par défaut). Un login faux est
  signalé dans le journal.
- `shorts.per_run` / le `cron` du workflow : le rythme de publication (3 par jour par défaut).
- `shorts.layout` et `streamers.<login>.layout` / `facecam` : forcer une mise en page, ou donner la position
  exacte de la facecam d'un streamer.
- `shorts.whisper_model` : `small` par défaut ; `large-v3-turbo` est plus précis mais plus lent.
- `blocklist` : les chaînes à ne jamais clipper.
- `queue.yaml` : tes URLs à passer en priorité (clip, VOD avec début/fin, vidéo YouTube).

## Droits d'auteur et règles des plateformes (important)

Les clips appartiennent aux streamers. Le clipping est très répandu et beaucoup de streamers
l'encouragent (certains rémunèrent même des clippeurs), mais ce n'est pas un droit. Le robot est conçu
pour en rester du bon côté :

- il **crédite** toujours le créateur (à l'image, dans la description, avec le lien du clip d'origine) ;
- il **transforme** : sélection, recadrage vertical, sous-titres, accroche, compilation commentée ;
- il **n'essaie pas de tromper la détection de droits** (pas de miroir ni de voix accélérée) : c'est
  volontaire, une chaîne qui contourne Content ID finit supprimée ;
- `blocklist` : si un streamer demande l'arrêt, ajoute-le et supprime ses vidéos ;
- la musique diffusée en live peut déclencher des revendications Content ID ; Claude écarte les extraits
  qui sont surtout de la musique, sans garantie.

Pour la monétisation YouTube, la règle sur le « contenu réutilisé » refuse souvent les chaînes de
compilations sans apport personnel : ajouter ta voix, tes réactions ou ton montage augmente beaucoup tes
chances. Lis aussi le règlement de chaque streamer (beaucoup indiquent leur position sur les clips).

## Coûts

- **GitHub Actions** : dépôt privé, 2 000 minutes gratuites par mois. Le robot en consomme environ 1 000
  (environ 8 minutes par passage, plus le best-of). Dépôt public : illimité.
- **Claude** : quelques centimes par passage (une requête : transcriptions + 3 images par candidat),
  soit environ 5 à 10 $ par mois au rythme par défaut. Facultatif.
- Tout le reste (Twitch, YouTube, TikTok, Telegram, Whisper, ffmpeg) est gratuit.

## Dépannage

| Symptôme | Solution |
| --- | --- |
| « Chaînes Twitch introuvables » | Corrige le login dans `config.yaml` (fin de l'URL `twitch.tv/…`). |
| « Twitch injoignable pour toutes les chaînes » | Panne réseau ou Twitch : le passage suivant réessaiera. |
| `YOUTUBE_REFRESH_TOKEN expiré` | Écran de consentement « En production », puis `python -m clipper auth youtube`. |
| Vidéos YouTube en privé | Audit de l'API pas encore accordé (voir YouTube, étape 5). |
| `quota quotidien` YouTube | Moins d'envois par jour, ou demande d'extension de quota. |
| TikTok `unaudited_client…` | `publish.tiktok.mode: inbox`, ou fais auditer l'app. |
| Sous-titres absents | Voir le journal : modèle Whisper non téléchargé, ou aucune parole détectée. |
| Mauvais cadrage d'un streamer | `streamers: { login: { layout: split, facecam: [x, y, l, h] } }`. |

## Développement

```bash
cd clipper && pip install -r requirements-dev.txt && python -m pytest
```

Les faux serveurs (Twitch, Google, TikTok, Telegram, GitHub, Anthropic) vivent uniquement dans `tests/`.
Ils prouvent la logique des clients, pas le comportement des vraies API.

Arborescence : `clipper/sources/` (Twitch, YouTube, file manuelle), `selection.py` (tri), `transcribe.py`
(Whisper), `captions.py` (sous-titres ASS), `facecam.py` (cadrage), `render.py` (short vertical),
`compilation.py` et `thumbnail.py` (best-of), `ai.py` (Claude), `publishers/` (YouTube, TikTok, Telegram),
`pipeline.py` (orchestration), `state.py` (mémoire), `cli.py` (commandes).

Crédits : police [Anton](https://github.com/googlefonts/AntonFont) (SIL Open Font License,
`assets/fonts/OFL.txt`), détecteur de visage [YuNet](https://github.com/opencv/opencv_zoo) (MIT,
`assets/models/LICENSE-yunet.txt`).
