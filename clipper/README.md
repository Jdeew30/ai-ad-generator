# Clipper : la chaîne de clips automatisée (Twitch / YouTube → YouTube Shorts, TikTok)

Un robot qui, plusieurs fois par jour :

1. **repère** les meilleurs moments de tout Twitch FR : les clips les plus vus des 7 derniers jours (ce sont
   les viewers eux-mêmes qui clippent les moments forts) des 51 streamers suivis, des 100 plus gros lives FR
   du moment, des chaînes FR vues ces 14 derniers jours (même hors ligne), et les clips FR les plus vus de la
   semaine dans les 20 catégories les plus regardées, quelle que soit la chaîne ; plus les passages « les
   plus revus » des nouvelles vidéos de 89 YouTubeurs FR de tous les domaines, de leurs autres chaînes vérifiées
   (« X Gaming », « X 2 »…) et des streamers FR dont il découvre la chaîne YouTube (flux RSS de chaque chaîne
   à chaque passage), et les URLs que tu ajoutes à la main ;
2. **trie** : anti-doublon (le même moment clippé par 10 viewers ne sort qu'une fois), jamais deux fois le
   même clip, 2 clips max par streamer et par jour, popularité *relative* au streamer, fraîcheur ;
3. **fait juger** les candidats par **Claude** (facultatif) : chaque clip Twitch est repris dans le replay du live
   avec 20 s avant et 8 s après (la mise en place et la fin de la réaction ; le clip seul si le replay est
   indisponible ou muet), puis Claude reçoit la transcription + 4 images de chaque extrait ; il
   écarte ce qui est risqué (haine, harcèlement, contenu sexuel, infos privées, musique protégée, films,
   matchs de foot… ; la rage et les gros mots passent), note chaque extrait sur 100 dans l'esprit des
   best-of Twitch FR comme « La Crème de Twitch » (moments drôles, annonces et actu du milieu, anecdotes,
   piques entre streamers, duos, exploits, réactions en co-stream d'e-sport, scènes de RP) et ne laisse
   passer que les vrais moments forts (60 minimum), coupe les temps morts et écrit
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
   **Telegram** avec sa fiche (créateur, chaîne, titre original, plateforme, date, durée, vues, likes, note de
   Claude et pourquoi, lien vers le passage) puis le texte prêt à coller, pour TikTok et en attendant l'audit ;
6. chaque mercredi et chaque dimanche, monte un **best-of** des 4 derniers jours (un clip n'est jamais repris d'un
   best-of à l'autre) en 16:9 d'environ 15 minutes (Claude retient des
   clips jusqu'à 15 min, jamais plus de 20 ; compte à rebours jusqu'au #1, bandeau avec le nom du streamer,
   carte d'intro, miniature générée, chapitres et crédits dans la description). Il pèse plusieurs centaines
   de Mo, bien plus que les 50 Mo qu'un bot Telegram peut envoyer : il est rangé en MP4 dans une
   « release » du dépôt (onglet **Releases**, les 4 derniers best-of sont gardés). Tu reçois sur Telegram la
   miniature, le titre et la description à coller, puis le lien direct du MP4. Le dépôt étant privé, ouvre
   ce lien dans ton navigateur connecté à ton compte GitHub : sans connexion (dans Telegram par exemple),
   GitHub répond « 404 ». Une fois YouTube branché, il est publié tout seul.

Tout tourne dans **GitHub Actions** (aucun serveur à louer). Le robot se souvient de ce qu'il a déjà publié
grâce à une branche dédiée `clipper-state`, qui contient aussi la **base des créateurs** (`creators.json`) et le
**tableau de bord** (`dashboard.md`, réécrit à chaque passage : moments trouvés, vidéos préparées, shorts envoyés,
échecs, sources et créateurs les plus repris, note moyenne, veille YouTube, derniers shorts) :
`https://github.com/Jdeew30/ai-ad-generator/blob/clipper-state/dashboard.md` (connecté à GitHub, dépôt privé).

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
| Sous-titres Whisper | ✅ Vérifié en réel | Modèle `small` sur de vrais clips en français : « Il a défoncé la main avec le crayon ! Il lui a planté la main ? Ah oui ! » ; « …mesdames et messieurs, veuillez accueillir ! ». Des erreurs restent possibles sur l'argot, les noms propres ou un fond musical fort ; les boucles que Whisper invente sur le son du jeu (« 9 .9 9 .9 ») et les mots dans un autre alphabet sont retirés des sous-titres. |
| Revue et textes par Claude | ✅ Vérifié en réel | Sur de vrais clips : écarte les extraits sans chute ou incompréhensibles hors du live (la raison est dans le résumé du workflow), coupe et écrit les textes. Note sur 100 : sous 60, le clip n'est pas publié. Sans clé : gabarit non-IA (titre du clip d'origine). |
| Publication YouTube | ⚠️ Écrite, testée contre un faux serveur | Envoi « resumable » par morceaux avec reprise après coupure. **Tant que ton projet Google n'a pas passé l'audit de l'API, YouTube verrouille les vidéos envoyées en privé** (voir plus bas). |
| Publication TikTok | ⚠️ Écrite, testée contre un faux serveur | Brouillon (« inbox ») ou publication directe. **En pratique, publication à la main depuis Telegram** : le règlement de l'API TikTok exclut les outils qui publient sur tes propres comptes et la reprise de contenus d'autres plateformes, donc pas d'audit, et sans audit tout reste privé (voir TikTok plus bas). |
| Envoi Telegram | ✅ Vérifié en réel | Chaque short arrive avec le texte à coller ; un message prévient quand un passage ne trouve rien d'assez bon. |
| Workflows GitHub Actions | ✅ Vérifié en réel | Un passage toutes les 2 heures de 9 h 37 à 23 h 37 (+ best-of du mercredi et du dimanche) sur ton dépôt. GitHub lance souvent les passages planifiés avec 30 à 60 minutes de retard. |

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
5. C'est tout : à chaque passage (vers 10 h, 13 h, 16 h, 19 h et 22 h à Paris l'hiver, une heure plus tard
   l'été), un short Twitch et un short YouTube, et un best-of le mercredi et le dimanche matin.

Le workflow sert aussi de télécommande depuis l'appli GitHub sur ton téléphone : **Run workflow** →
mode `url` → colle l'URL d'un clip ou d'une vidéo (avec début et fin si besoin) → il est monté et publié.
Avec un début **et** une fin, le passage choisi est gardé en entier (Claude écrit les textes sans recouper).
Tu ne connais pas le minutage ? Mets une phrase dite dans le passage dans le champ **find** (ex. `c'est quoi la
plus grosse insulte`) : Whisper écoute la vidéo, ou seulement de **début** à **fin** si tu les remplis (plus
rapide sur une longue vidéo), trouve la phrase même dite un peu autrement, et le robot garde 15 s avant et 35 s
après.

Pour **rallonger** un short dont le live n'a plus de replay : mode `clips` → colle le lien du clip Twitch. Le
résumé du passage liste les autres clips de la chaîne faits 10 minutes avant ou après (souvent le même moment,
parfois plus long ou commencé plus tôt), avec leur durée et leur lien. Relance ensuite le mode `url` avec le
lien choisi (début `0`, fin = sa durée), ou avec **plusieurs liens qui se suivent**, séparés par des espaces :
le robot les recolle en une seule vidéo, calée sur le son, où la partie commune n'apparaît qu'une fois. La
vidéo recollée est gardée en entier. Pour la **POV d'un autre streamer** présent au même moment, mets sa chaîne
dans le champ **focus** du mode `clips` (ex. `hctuan`) : ce sont alors ses clips qui sont listés, avec son
replay quand il existe (mode `url` avec `https://www.twitch.tv/videos/<replay>`, un début et une fin).

Pour un passage **ciblé** : mode `shorts` → champ **focus**, avec des chaînes Twitch, des chaînes YouTube
(`@nom`) ou des liens de vidéos YouTube, séparés par des virgules. Par exemple `gotaga, aminematue` ou le
lien du replay d'un événement. Seules ces sources sont regardées : les clips Twitch de la semaine (50 par
chaîne) et les 6 passages les plus revus de chaque vidéo YouTube, quels que soient son âge et ses vues. Claude
choisit toujours, avec la même note minimale. Le plafond par streamer ne compte pas, puisque tu as demandé ces
chaînes. Le champ **count** fixe le nombre de shorts (1 si vide). Les passages planifiés, eux, regardent
toujours tout Twitch FR et tous les YouTubeurs de la liste.

Case **force** (mode `shorts`) : quand tu veux un clip quoi qu'il arrive. Si aucun clip examiné n'atteint la note
minimale, le mieux noté part quand même, et Telegram précise sa note. Un clip que Claude refuse (contenu interdit,
moment déjà publié) ne part jamais.

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
vidéo et sa fiche, puis le texte à coller dans un bloc qui se copie d'un toucher ; tu enregistres, tu publies
(ou tu programmes dans TikTok Studio).

La « note » sur 100 est celle que Claude donne au passage : une estimation de son intérêt (accroche, réaction,
humour, surprise, compréhensible sans contexte…), pas une mesure de ses futures vues. Un envoi refusé pour
excès de messages (erreur 429) attend le délai que Telegram indique ; une coupure réseau est retentée. Une
vidéo de plus de 50 Mo est recompressée (débit réparti sur sa durée) ; si elle reste trop lourde, tu reçois la
fiche et le lien sans la vidéo. Pour envoyer des fichiers jusqu'à 2 Go, un serveur Bot API local
(`telegram-bot-api`) peut être indiqué dans le secret `TELEGRAM_API_URL` (ex. `http://mon-serveur:8081`).

### Ligne éditoriale : faire percer le compte

Constat du 05/11 sur les 71 shorts envoyés :
- **Clips Twitch trop vieux** : 67 h d'âge médian à l'envoi, alors que les autres comptes de clips postent les
  moments forts dans les heures qui suivent le live.
- **Clips Twitch trop confidentiels** : 600 vues médianes, 41 sur 43 sous 2 000 vues.
- **Peu de bons moments examinés** : 3 % des clips Twitch examinés atteignaient 60/100.

Le robot vise donc :
- **Le moment qui part maintenant** : clips classés par vues à l'heure, et un passage toutes les 2 heures. La
  fiche Telegram dit « ⚡ Tout frais : à poster maintenant » quand le moment a moins de 12 h. Les pépites de petits
  streamers restent dans la course une semaine : le short le plus vu du compte (Loupiote, « je regarde le ciel
  depuis deux ans, rien ne m'est tombé dessus », 75,6k vues sur TikTok) était un clip de 1 000 vues vieux de
  7 jours, que personne d'autre n'avait posté.
- **Des moments forts seulement** : plus de clip envoyé sous la barre de 60 « pour garder un short ». Claude note
  le potentiel viral : un inconnu s'arrête-t-il dès la première seconde, comprend-il en trois secondes, a-t-il
  envie de partager ?
- **Des shorts courts et nets** : 12 à 35 secondes visées, 60 au plus. Le short démarre sur l'action, finit
  juste après la chute (il se revoit en boucle), et son accroche ouvre une question sans révéler la chute.

### Claude : le tri et les textes par IA

Crée une clé sur [platform.claude.com](https://platform.claude.com) → secret `ANTHROPIC_API_KEY`.
Modèle par défaut : `claude-opus-5-5`, effort `medium`, avec le repli automatique côté serveur si une
demande est déclinée. Une requête par série de candidats (`shorts.review_rounds` séries au plus par source).
Les crédits de l'API se rechargent sur [console.anthropic.com](https://console.anthropic.com) (Plans &
Billing) : sans crédit, aucun clip ne peut être noté, donc aucun n'est envoyé. Le passage s'arrête alors dès
la première requête refusée (sans préparer d'autres clips pour rien) et le dit sur Telegram ; les clips non
notés sont réexaminés aux passages suivants.

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
   1 600, c'est juste assez pour les 5 shorts Twitch + un best-of (les extraits YouTube n'y sont pas renvoyés).
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

Activée : 89 YouTubeurs FR de tous les domaines (`youtube.channels`), suivis par **flux RSS** (`watch: rss`) :
à chaque passage, le robot lit le flux de *chaque* chaîne (15 dernières vidéos avec vues et likes ; gratuit,
sans quota d'API ni cookies). Il garde l'historique des vidéos vues et leurs vues à chaque passage, ce qui
mesure la **vitesse** à laquelle une vidéo prend des vues, comparée à l'habitude de la chaîne. Chaque nouvelle
vidéo des 14 derniers jours (`max_age_days`, fenêtre de rattrapage après une interruption) reçoit un
**pré-score** sur 100 (vitesse des vues, vues en absolu, likes, fraîcheur, titre qui promet un moment : réaction,
clash, première fois, record…) ; seules les 24 plus prometteuses (`videos_per_run`) sont regardées de près
(courbe « les plus revus » ou commentaires), les autres attendent leur tour. Une vidéo qui n'a rien donné est
revue une fois le lendemain (courbe et commentaires arrivent avec les vues). Une chaîne illisible trois passages
de suite est mise de côté.

La **base des créateurs** (`creators.json` sur la branche `clipper-state`) garde pour chaque créateur ses chaînes :
identifiant YouTube, adresse, titre, abonnés, catégories YouTube de ses vidéos, dernière lecture, statut, et
comment la chaîne a été vérifiée. Les chaînes de `config.yaml` sont les chaînes principales. Avec
`related_channels`, le robot cherche aussi les autres chaînes de chaque YouTubeur (« Squeezie Gaming »,
« … Vlog », « … 2 ») : seulement les noms « <nom> <suffixe> » avec assez d'abonnés, et une telle chaîne n'est
**suivie qu'une fois vérifiée** : sa description cite la chaîne principale (son @, son adresse), ou la description
de la chaîne principale la cite. Les autres restent « non vérifiées » et sont revues deux semaines plus tard.
Le robot **découvre aussi de nouveaux créateurs** : pour les streamers FR vus en live sur Twitch ces deux derniers
jours, il cherche une chaîne YouTube du même nom et ne la suit que si sa description renvoie vers leur Twitch
(`twitch.tv/<login>`) ; 3 recherches par passage (`discover_per_run`), une par streamer et par mois au plus.
Une chaîne dont le @ n'existe pas est cherchée par son nom (« @LeWoop » → la chaîne « Le Woop », la
plus suivie de ce nom exact) et le journal du passage donne le bon @ à mettre dans `config.yaml` ; une chaîne
sans onglet « Vidéos » (que des lives) donne ses lives. Le robot prend
leurs vidéos des deux dernières semaines (`max_age_days`, plus de 20 000 vues, pour ne pas écarter les chaînes
secondaires) et découpe les passages « les plus revus ».
Quand YouTube ne fournit pas cette courbe, il lit les 60 commentaires les plus appréciés de la vidéo et prend les
moments que les spectateurs citent (« 12:34 mdr »), d'autant plus qu'ils sont cités et aimés : 20 s avant
l'instant cité et 70 s après, pour avoir la mise en place et la chute. Claude reçoit le commentaire qui cite le
moment, le cherche dans l'extrait et coupe autour de lui. Le journal du passage
compte les vidéos écartées et dit pourquoi (trop ancienne, pas assez de vues, ni courbe ni minutage en commentaire…).

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
(`publish.youtube.youtube_sources: false`) et Telegram te conseille de le poster sur TikTok. Les best-of
restent 100 % Twitch.

## Réglages utiles (`config.yaml`)

- `twitch.streamers` : la liste des logins suivis (51 streamers FR par défaut). Un login faux est signalé
  dans le journal. Avec les clés Twitch, `discover_limit` y ajoute les plus gros lives FR du moment (100),
  `remember_live` les chaînes FR vues ces 14 derniers jours (150 au plus) et `discover_categories` les clips
  FR les plus vus de la semaine dans les catégories les plus regardées (20).
- `shorts.per_source` / le `cron` du workflow : le rythme de publication, jusqu'à deux shorts Twitch et un short
  YouTube par passage, un passage toutes les 2 heures de 9 h 37 à 23 h 37 (seuls les moments notés 60 ou plus
  partent). `shorts.review_rounds` : séries de candidats examinées au plus par source et par passage (2 : le
  passage suivant prend la suite, et les crédits Claude restent ceux de 5 passages de 4 séries).
  `shorts.guarantee` (vide depuis le 05/11) peut garder un short de chaque source à chaque passage, même sous
  60 : il envoyait des clips jugés faibles. Sans `per_source`, `shorts.per_run` shorts par passage.
- `youtube.videos_per_run`, `youtube.max_age_days` : combien de nouvelles vidéos YouTube sont regardées de près
  par passage (24 : plus de moments parmi lesquels choisir), et la fenêtre de rattrapage. Le dépôt est public :
  les minutes GitHub Actions sont illimitées, mais chaque passage coûte des crédits Claude (la revue).
- `shorts.min_score` : la note de Claude (sur 100) qu'un clip doit avoir pour être publié, 60 par défaut.
  Plus haut : moins de shorts mais meilleurs.
- `twitch.window_hours` / `twitch.min_views` : l'âge maximal des clips (7 jours) et le nombre de vues minimal
  (150 : c'est la note de Claude qui écarte les moments faibles). Les clips Twitch sont classés d'abord par vues
  à l'heure : celui qui part maintenant passe avant un clip plus vu mais vieux de trois jours, que les autres
  comptes ont déjà posté. Un clip qui fait bien plus que d'habitude pour son streamer garde un bonus : la pépite
  d'un petit streamer, que personne d'autre ne poste, reste bonne une semaine.
- Le replay autour d'un clip Twitch arrive d'habitude en quelques secondes : s'il n'est pas là au bout d'une
  minute, le clip part seul. Un clip dont la préparation dépasse quand même 4 minutes est abandonné pour de bon :
  le passage prend le suivant, et ne prépare plus rien après 55 minutes pour envoyer à temps ce qu'il a retenu.
- `twitch.skip_title_words` : les mots qui font écarter un clip avant tout téléchargement (pubs :
  « publicité », « sponsorisé »…), Claude les refusant de toute façon.
- `twitch.skip_live_words` : pareil, mais d'après le titre du **live** dont le clip est tiré. Vide par défaut :
  les co-streams d'e-sport (Worlds, RLCS…) sont les bienvenus, Claude y juge la réaction du streamer.
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

- **GitHub Actions** : dépôt privé, 2 000 minutes gratuites par mois. Depuis YouTube (lecture des
  commentaires) et ses deux shorts par passage, un passage dure de 10 à 25 minutes, et chaque best-of jusqu'à
  1 h 30 : le robot dépasse les 2 000 minutes. Dépôt public : illimité (et les liens du best-of s'ouvrent
  alors sans connexion).
- **Stockage GitHub** : 500 Mo gratuits pour les vidéos gardées par les passages (« artefacts »), et plus
  aucun dépôt possible au-delà. Les shorts, déjà sur Telegram, ne sont donc gardés qu'un jour, et chaque
  passage supprime les vidéos des passages plus anciens. Le best-of va dans les releases, hors de ce quota.
- **Claude** : une requête par série de candidats (avec transcriptions et images), soit 10 à 40 centimes par
  passage et environ 1 à 3 $ par jour au rythme par défaut, selon le nombre de séries (estimation : le chiffre
  exact est dans console.anthropic.com, rubrique Usage). Facultatif.
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
