# Clipper : la chaîne de clips automatisée (Twitch / YouTube → YouTube Shorts, TikTok)

Un robot qui, plusieurs fois par jour :

1. **repère** les meilleurs moments de tout Twitch FR : les clips les plus vus des 7 derniers jours (ce sont
   les viewers eux-mêmes qui clippent les moments forts) des 51 streamers suivis, des 100 plus gros lives FR
   du moment, des chaînes FR vues ces 14 derniers jours (même hors ligne), et les clips FR les plus vus de la
   semaine dans les 20 catégories les plus regardées, quelle que soit la chaîne ; plus les passages « les
   plus revus » des vidéos de 68 YouTubeurs FR de tous les domaines et de leurs autres chaînes (« X Gaming »,
   « X 2 »…), à tour de rôle, et les URLs que tu
   ajoutes à la main ;
2. **trie** : anti-doublon (le même moment clippé par 10 viewers ne sort qu'une fois), jamais deux fois le
   même clip, 2 clips max par streamer et par jour, popularité *relative* au streamer, fraîcheur ;
3. **fait juger** les candidats par **Claude** (facultatif) : chaque clip Twitch est repris dans le replay du live
   avec 20 s avant et 8 s après (la mise en place et la fin de la réaction ; le clip seul si le replay est
   indisponible ou muet), puis Claude reçoit la transcription + 4 images de chaque extrait ; il
   écarte ce qui est risqué (insultes, contenu sexuel, infos privées, musique protégée, retransmissions
   officielles…), note chaque extrait sur 100 dans l'esprit des best-of Twitch FR comme « La Crème de Twitch »
   (moments drôles, annonces et actu du milieu, anecdotes, piques entre streamers, duos, exploits) et ne
   laisse passer que les vrais moments forts (60 minimum), coupe les temps morts et écrit
   l'accroche, le titre, la description et les hashtags. Il connaît les shorts publiés ces 3 derniers jours :
   plusieurs parties d'un même live ou d'une même histoire sont possibles (« (partie 2) » dans le titre),
   jamais deux fois le même moment, même filmé par un autre streamer. Chaque série met en concurrence des
   streamers différents ; si rien n'est assez bon, il examine une nouvelle série (4 séries au plus), puis
   préfère ne rien publier et te prévient sur Telegram ;
4. **monte** chaque short en 1080×1920 : l'image entière, nette au milieu, sur un fond flouté (réglage choisi
   pour la chaîne) ; en mode `auto`, facecam en haut et jeu en bas (détection automatique du visage) ou plein
   cadre centré sur le streamer selon le clip ; **sous-titres animés mot à mot** (Whisper),
   accroche incrustée, crédit `twitch.tv/<streamer>`, son normalisé ;
5. **publie** sur YouTube Shorts (une fois l'audit de Google accepté) et t'envoie chaque vidéo sur
   **Telegram** avec le texte prêt à coller, pour TikTok et en attendant l'audit ;
6. chaque dimanche, monte un **best-of de la semaine** en 16:9 (compte à rebours #12 → #1, bandeau avec le
   nom du streamer, carte d'intro, miniature générée, chapitres et crédits dans la description). Il pèse
   quelques centaines de Mo, bien plus que les 50 Mo qu'un bot Telegram peut envoyer : tu reçois sur
   Telegram la miniature, le lien pour le télécharger (section « Artifacts » du passage, gardé 7 jours) et
   un message avec le titre et la description à coller. Une fois YouTube branché, il est publié tout seul.

Tout tourne gratuitement dans **GitHub Actions** (aucun serveur à louer). Le robot se souvient de ce qu'il
a déjà publié grâce à une branche dédiée `clipper-state`.

Chaînes : [YouTube](https://www.youtube.com/channel/UC7iURKSML3pyYVII8bbcRUw) ·
[TikTok @tesmeilleursclips](https://www.tiktok.com/@tesmeilleursclips).

## État réel (à lire avant de compter dessus)

| Fonction | État | Preuve / limite |
| --- | --- | --- |
| Montage vertical (3 mises en page, sous-titres, accroche, crédit) | ✅ Vérifié | Vrai ffmpeg sur des vidéos de synthèse avec un vrai visage : dimensions, durée, son, mot surligné présent à l'image, visage retrouvé dans le panneau facecam. Images contrôlées à l'œil. |
| Détection facecam / plein cadre / sans visage | ✅ Vérifié, y compris en réel | YuNet (OpenCV) sur une vraie photo incrustée en facecam ou en plein cadre. Sur de vrais clips d'AmineMaTue : caméra plein cadre reconnue (visage sur 8 images sur 8 → cadrage centré sur lui) ; sur un clip de réaction, visages vus sur 5 images sur 8 mais à des endroits différents (les personnes de la vidéo regardée) → pas de facecam stable → « fond flouté », volontairement. Une facecam très petite ou de profil peut ne pas être détectée : on peut alors donner sa position exacte par streamer. |
| Best-of 16:9, miniature, chapitres YouTube | ✅ Vérifié | Vrai ffmpeg + Pillow ; règles des chapitres (0:00, 3 minimum, 10 s minimum) testées. |
| Anti-doublons, quotas par streamer, mémoire entre passages | ✅ Vérifié | Tests de bout en bout sur 4 passages successifs, et en réel : sur 61 clips du moment, doublons d'un même moment et quota par streamer écartés. |
| Découverte Twitch sans clés (yt-dlp) | ✅ Vérifié en réel | Job `smoke` de **Clipper CI** (sur les serveurs de GitHub) : 61 clips trouvés sur les 24 chaînes, les meilleurs téléchargés, transcrits et montés en shorts (artefact du workflow). |
| API Twitch officielle (avec clés) | ✅ Vérifié en réel | Avec tes clés : les chaînes suivies et les plus gros lives FR du moment, plusieurs centaines de clips de la semaine examinés à chaque passage. |
| Sous-titres Whisper | ✅ Vérifié en réel | Modèle `small` sur de vrais clips en français : « Il a défoncé la main avec le crayon ! Il lui a planté la main ? Ah oui ! » ; « …mesdames et messieurs, veuillez accueillir ! ». Des erreurs restent possibles sur l'argot, les noms propres ou un fond musical fort. |
| Revue et textes par Claude | ✅ Vérifié en réel | Sur de vrais clips : écarte les extraits sans chute ou incompréhensibles hors du live (la raison est dans le résumé du workflow), coupe et écrit les textes. Note sur 100 : sous 60, le clip n'est pas publié. Sans clé : gabarit non-IA (titre du clip d'origine). |
| Publication YouTube | ⚠️ Écrite, testée contre un faux serveur | Envoi « resumable » par morceaux avec reprise après coupure. **Tant que ton projet Google n'a pas passé l'audit de l'API, YouTube verrouille les vidéos envoyées en privé** (voir plus bas). |
| Publication TikTok | ⚠️ Écrite, testée contre un faux serveur | Brouillon (« inbox ») ou publication directe. **En pratique, publication à la main depuis Telegram** : le règlement de l'API TikTok exclut les outils qui publient sur tes propres comptes et la reprise de contenus d'autres plateformes, donc pas d'audit, et sans audit tout reste privé (voir TikTok plus bas). |
| Envoi Telegram | ✅ Vérifié en réel | Chaque short arrive avec le texte à coller ; un message prévient quand un passage ne trouve rien d'assez bon. |
| Workflows GitHub Actions | ✅ Vérifié en réel | 5 passages par jour (+ best-of du dimanche) sur ton dépôt. GitHub lance souvent les passages planifiés avec 30 à 45 minutes de retard. |

**Le premier passage avec tes vrais comptes est la vraie validation des publications.** Toute erreur
(quota, jeton expiré, refus de la plateforme) apparaît telle quelle dans le résumé du workflow et sur
Telegram ; rien n'est marqué « publié » sans réponse positive de la plateforme.

## Démarrage rapide

### Sur GitHub (recommandé : 100 % automatique)

1. Le workflow doit être sur la branche par défaut du dépôt (le planning ne tourne que depuis celle-ci).
2. Va dans **Actions → Clipper → Run workflow**, coche **Essai à blanc** et lance. Au bout de quelques
   minutes, le short monté est téléchargeable dans les **Artifacts** du run. Sans aucun identifiant, le
   robot fonctionne déjà : clips Twitch via yt-dlp, textes par gabarit, vidéos en artefacts.
3. Ajoute les secrets au fur et à mesure, dans l'ordre conseillé : Telegram, Claude, Twitch, puis YouTube
   une fois l'audit de Google accepté (détails plus bas, tout se fait depuis un navigateur). TikTok se
   publie à la main depuis Telegram.
4. Vérifie : **Run workflow** → mode **`check`**. Le résumé du run affiche ✅ / ❌ pour chaque service, et
   Telegram reçoit un message de test. Rien n'est publié.
5. C'est tout : 5 shorts par jour (vers 10 h, 13 h, 16 h, 19 h et 22 h à Paris l'hiver, une heure plus tard l'été)
   et le best-of le dimanche matin.

Le workflow sert aussi de télécommande depuis l'appli GitHub sur ton téléphone : **Run workflow** →
mode `url` → colle l'URL d'un clip ou d'une vidéo (avec début et fin si besoin) → il est monté et publié.

Pour un passage **ciblé** : mode `shorts` → champ **focus**, avec des chaînes Twitch, des chaînes YouTube
(`@nom`) ou des liens de vidéos YouTube, séparés par des virgules. Par exemple `gotaga, aminematue` ou le
lien du replay d'un événement. Seules ces sources sont regardées : les clips Twitch de la semaine (50 par
chaîne) et les 6 passages les plus revus de chaque vidéo YouTube, quels que soient son âge et ses vues. Claude
choisit toujours, avec la même note minimale. Le plafond par streamer ne compte pas, puisque tu as demandé ces
chaînes. Le champ **count** fixe le nombre de shorts (1 si vide). Les passages planifiés, eux, regardent
toujours tout Twitch FR et tous les YouTubeurs de la liste.

### Sur ton ordinateur (pour tester ou pour YouTube)

Prérequis : Python 3.10+ et ffmpeg (`winget install ffmpeg`, `brew install ffmpeg` ou `apt install ffmpeg`).

```bash
cd clipper
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # puis remplis ce que tu as
python -m clipper doctor                            # vérifie l'installation
python -m clipper check                             # essaie chaque identifiant, sans rien publier
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

**Ajouter un secret** : sur la page du dépôt, **Settings → Secrets and variables → Actions → New repository
secret**, nom exactement comme indiqué ci-dessous (ex. `TELEGRAM_BOT_TOKEN`), valeur collée telle quelle.

### Telegram : recevoir les vidéos sur ton téléphone (2 min)

1. Dans Telegram, écris à **@BotFather** → `/newbot` → note le jeton (`TELEGRAM_BOT_TOKEN`).
2. Écris `/start` à ton nouveau bot.
3. Ouvre dans un navigateur `https://api.telegram.org/bot<TON_JETON>/getUpdates` (en remplaçant
   `<TON_JETON>`) : le nombre après `"chat":{"id":` est ton `TELEGRAM_CHAT_ID`. (En local :
   `python -m clipper auth telegram` l'affiche aussi.)

Le bot ne répond pas aux messages, c'est normal : il sert seulement à t'envoyer les vidéos. C'est la voie
normale pour TikTok (voir plus bas), et pour YouTube tant que l'audit n'est pas accepté : tu reçois la
vidéo et la légende, tu enregistres, tu publies (ou tu programmes dans TikTok Studio).

### Claude : le tri et les textes par IA

Crée une clé sur [platform.claude.com](https://platform.claude.com) → secret `ANTHROPIC_API_KEY`.
Modèle par défaut : `claude-opus-5-5`, effort `medium`, avec le repli automatique côté serveur si une
demande est déclinée. Une requête par série de candidats (quatre séries au plus par passage).

### Twitch : la découverte des lives (5 min, gratuit)

Sans clés, le robot lit les clips des streamers de `config.yaml` via yt-dlp. Avec les clés, il utilise
l'API officielle, **ajoute automatiquement les plus gros lives FR du moment**, et connaît la position de
chaque clip dans la VOD (anti-doublon plus précis).

1. Active la double authentification sur ton compte Twitch.
2. [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) → **Register Your Application** →
   URL de redirection `https://localhost` (Twitch exige du HTTPS ; le robot ne s'en sert jamais),
   type « Confidential ».
3. Secrets `TWITCH_CLIENT_ID` et `TWITCH_CLIENT_SECRET` (bouton « New Secret »).

### YouTube : publication automatique

1. [console.cloud.google.com](https://console.cloud.google.com) → crée un projet → **API et services →
   Bibliothèque** → active **YouTube Data API v3**.
2. **Google Auth Platform** (l'ancien « écran de consentement OAuth ») → Commencer : nom de l'appli, ton
   e-mail, audience « Externe ». Puis **Audience → Publier l'application** (statut « En production »). En
   mode « Test », Google invalide le jeton au bout de 7 jours et le robot s'arrête. Pour un usage perso, pas
   besoin de faire vérifier l'app : à la connexion, clique « Paramètres avancés → Accéder à … ».
3. **Google Auth Platform → Clients → Créer un client → Application Web**, avec comme URI de redirection
   autorisé `https://developers.google.com/oauthplayground` → `YOUTUBE_CLIENT_ID` et `YOUTUBE_CLIENT_SECRET`.
   Copie le code secret tout de suite : la console ne le réaffiche plus ensuite.
4. Le refresh token, sans rien installer : ouvre
   [developers.google.com/oauthplayground](https://developers.google.com/oauthplayground) → roue dentée en
   haut à droite → coche **Use your own OAuth credentials** et colle l'ID client et le secret → dans
   « Input your own scopes », saisis `https://www.googleapis.com/auth/youtube.upload` → **Authorize APIs** →
   connecte le compte **de la chaîne** → **Exchange authorization code for tokens** → copie le
   **Refresh token** dans le secret `YOUTUBE_REFRESH_TOKEN`. (Avec tes propres identifiants, le Playground
   ne révoque pas le jeton.) Variante en local : un ID client « Application de bureau » et
   `python -m clipper auth youtube`.
5. ⚠️ **Audit obligatoire pour publier en public** : les vidéos envoyées par un projet non audité sont
   verrouillées en privé, définitivement. Remplis le formulaire
   [YouTube API Services – Audit and Quota Extension](https://support.google.com/youtube/contact/yt_api_form)
   (gratuit, quelques jours à quelques semaines, sans garantie d'accord) et **n'ajoute les trois secrets
   `YOUTUBE_*` qu'une fois l'audit accepté**. En attendant, publie à la main depuis Telegram ou les
   artefacts.
6. Quota : 10 000 unités par jour par défaut. Un envoi a longtemps coûté 1 600 unités (environ 6 envois par
   jour) ; Google aurait fortement baissé ce coût fin 2025, vérifie le chiffre actuel dans la console. Même à
   1 600, c'est juste assez pour 5 shorts + le best-of du dimanche.
   Les miniatures personnalisées exigent une chaîne validée par téléphone
   ([youtube.com/verify](https://www.youtube.com/verify)).

### TikTok : publication à la main (et option avancée)

Le règlement de l'API de publication de TikTok exclut les outils qui publient sur tes propres comptes et
les applis qui reprennent des contenus d'autres plateformes. TikTok n'auditera donc pas ce robot, et sans
audit, tout ce qui passe par son API reste privé. **La voie normale, c'est Telegram** : la vidéo et sa
légende arrivent sur ton téléphone, tu publies en une minute (avec un son tendance si tu veux).

Option avancée, **jamais testée en réel** : recevoir chaque vidéo en brouillon dans l'appli TikTok, via une
app développeur restée en mode *sandbox* (pas de validation TikTok, 10 comptes de test au maximum).

1. [developers.tiktok.com](https://developers.tiktok.com) → crée une app, puis une **sandbox** → ajoute ton
   compte TikTok dans ses comptes cibles (*target users*).
2. Ajoute **Login Kit** (plateforme Web) avec une URL de redirection HTTPS sur un site que tu contrôles (ce
   dépôt est privé : GitHub Pages n'y est pas disponible sur un compte gratuit), et **Content Posting API**
   en envoi de brouillons, avec les scopes `user.info.basic` et `video.upload`.
3. Sur ton ordinateur : `python -m clipper auth tiktok --redirect-uri https://…` → ouvre le lien, accepte,
   puis colle l'adresse de la page d'arrivée → secrets `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`,
   `TIKTOK_REFRESH_TOKEN`.
4. Mode **`inbox`** (défaut) : la vidéo arrive en brouillon, tu publies d'un tap. TikTok limite le nombre de
   brouillons en attente. Le mode **`direct`** demande en plus le scope `video.publish` et un audit que
   TikTok n'accorde pas à ce type d'outil (erreur `unaudited_client_can_only_post_to_private_accounts`, que
   le robot explique).
5. TikTok peut renouveler le refresh token. Pour que le robot l'enregistre seul, ajoute le secret
   `GH_SECRETS_TOKEN` : un jeton GitHub *fine-grained* limité à ce dépôt avec la permission
   « Secrets : Read and write ». Sans lui, le journal te prévient s'il faut mettre le secret à jour.

### YouTube comme source : les YouTubeurs FR

Activée : 68 YouTubeurs FR de tous les domaines (`youtube.channels`), 8 chaînes par passage à tour de rôle
(`channels_per_run`). Avec `related_channels`, le robot cherche aussi les autres chaînes de chaque YouTubeur
(« Squeezie Gaming », « Djilsi Gaming », « … Vlog », « … 2 ») : seulement les noms « <nom> <suffixe> » avec
assez d'abonnés, donc ni les chaînes de fans ni les chaînes « Topic » ; 3 recherches par passage, gardées
14 jours. Une chaîne introuvable est signalée dans le journal du passage. Le robot
prend leurs vidéos de la semaine (plus de 50 000 vues) et découpe les passages « les plus revus ».

YouTube bloque parfois les serveurs de GitHub (« Sign in to confirm you're not a bot ») : passé le 29/10,
bloqué le 30/10 au matin. Le passage laisse alors YouTube de côté, et s'il ne trouve rien du tout, Telegram
te le dit. Pour lever le blocage, fournis des cookies YouTube dans le secret `YTDLP_COOKIES` (format
Netscape), d'un **compte Google secondaire** :
1. sur un ordinateur, ouvre une fenêtre de navigation privée et connecte-toi à YouTube avec ce compte ;
2. dans cette fenêtre, ouvre `https://www.youtube.com/robots.txt` dans un nouvel onglet et ferme l'onglet YouTube ;
3. exporte les cookies avec l'extension « Get cookies.txt LOCALLY » (à autoriser en navigation privée) ;
4. ferme la fenêtre privée sans te déconnecter, et colle tout le fichier dans le secret `YTDLP_COOKIES`.

**Content ID** : un extrait de vidéo YouTube republié sur YouTube est presque toujours repéré (revenus
reversés au YouTubeur, blocage, voire avertissement). Le robot ne le republie donc jamais sur YouTube
(`publish.youtube.youtube_sources: false`) et Telegram te conseille de le poster sur TikTok. Le best-of du
dimanche reste 100 % Twitch.

## Réglages utiles (`config.yaml`)

- `twitch.streamers` : la liste des logins suivis (51 streamers FR par défaut). Un login faux est signalé
  dans le journal. Avec les clés Twitch, `discover_limit` y ajoute les plus gros lives FR du moment (100),
  `remember_live` les chaînes FR vues ces 14 derniers jours (150 au plus) et `discover_categories` les clips
  FR les plus vus de la semaine dans les catégories les plus regardées (20).
- `shorts.per_run` / le `cron` du workflow : le rythme de publication (5 par jour par défaut).
- `shorts.min_score` : la note de Claude (sur 100) qu'un clip doit avoir pour être publié, 60 par défaut.
  Plus haut : moins de shorts mais meilleurs.
- `twitch.window_hours` / `twitch.min_views` : l'âge maximal des clips (7 jours) et le nombre de vues minimal.
- `twitch.skip_title_words` : les mots qui font écarter un clip avant tout téléchargement (watch parties,
  pubs : « worlds », « publicité »…), Claude les refusant de toute façon.
- `twitch.skip_live_words` : pareil, mais d'après le titre du **live** dont le clip est tiré : un co-stream des
  Worlds ou une watch party montre surtout la retransmission officielle, qui n'appartient pas au streamer.
- `shorts.context_before` / `context_after` : les secondes de replay ajoutées autour de chaque clip (20 et 8),
  que Claude garde ou coupe. `0` pour travailler sur le clip seul.
- `shorts.layout` : `blur` (image entière sur fond flouté, réglage de la chaîne), ou `auto` pour laisser le
  robot choisir entre facecam en haut / jeu en bas, plein cadre et fond flouté. `streamers.<login>.layout` /
  `facecam` : une mise en page ou la position exacte de la facecam pour un streamer précis.
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

- **GitHub Actions** : dépôt privé, 2 000 minutes gratuites par mois. Le robot en consomme environ 1 300
  (5 passages par jour de 6 à 8 minutes, plus le best-of et les tests). Dépôt public : illimité.
- **Stockage GitHub** : 500 Mo gratuits pour les vidéos gardées par les passages (« artefacts »), et plus
  aucun dépôt possible au-delà. Les shorts, déjà sur Telegram, ne sont donc gardés qu'un jour, le best-of
  7 jours, et chaque passage supprime les vidéos des passages plus anciens.
- **Claude** : quelques centimes par passage (une requête par série de 3 candidats, avec transcriptions et
  images), soit environ 10 à 30 $ par mois au rythme par défaut selon le nombre de séries. Facultatif.
- Tout le reste (Twitch, YouTube, TikTok, Telegram, Whisper, ffmpeg) est gratuit.

## Dépannage

| Symptôme | Solution |
| --- | --- |
| « Chaînes Twitch introuvables » | Corrige le login dans `config.yaml` (fin de l'URL `twitch.tv/…`). |
| « Twitch injoignable pour toutes les chaînes » | Panne réseau ou Twitch : le passage suivant réessaiera. |
| `YOUTUBE_REFRESH_TOKEN expiré` | Écran de consentement « En production », puis `python -m clipper auth youtube`. |
| Vidéos YouTube en privé | Audit de l'API pas encore accordé (voir YouTube, étape 5). |
| `quota quotidien` YouTube | Moins d'envois par jour, ou demande d'extension de quota. |
| TikTok `unaudited_client…` | `publish.tiktok.mode: inbox` (TikTok n'audite pas ce type d'outil). |
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
