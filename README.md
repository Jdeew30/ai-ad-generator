# AI Ad Studio

SaaS qui transforme un **brief produit** en **concept publicitaire** puis en **vidéo**, en pilotant un fournisseur de génération vidéo IA que vous avez connecté.

> **Principe : zéro simulation.** Une vidéo *générée par IA* n'existe que si un fournisseur réel l'a produite. Aucun fournisseur « mock » n'est expédié ; sans clé API, la génération par IA est désactivée et l'interface le dit. Une seule exception, explicite et étiquetée partout « gratuit · sans IA » : le **montage de vos photos** (voir plus bas), qui n'invente rien et ne prétend pas être de l'IA. Un bouton « Télécharger » n'apparaît que lorsqu'un fichier vidéo a réellement été reçu et enregistré.

Stack : Next.js 16 (App Router) · React 19 · TypeScript strict · Tailwind CSS v4 · Prisma 7 + PostgreSQL · Better Auth · Zod 4 · SDK Anthropic.
Architecture détaillée : [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## État réel (à lire avant d'utiliser)

| Fonctionnalité | État | Preuve / limite |
| --- | --- | --- |
| Auth e-mail + mot de passe, sessions en base, rate limiting | ✅ Fonctionne | Vérifié en navigateur (inscription, limiteur déclenché pour de vrai). **Pas** de vérification d'e-mail ni de reset de mot de passe (aucun service e-mail branché). |
| Brief (assistant 3 étapes), édition, suppression | ✅ Fonctionne | Vérifié en navigateur. |
| Upload logo / photos (PNG, JPEG, WebP) | ✅ Fonctionne | Type détecté par *magic bytes*, SVG refusé, 8 Mo max, tests dédiés. |
| Concept avec **Claude** (sortie structurée) | ⚠️ Écrit, **non testé contre l'API réelle** | Testé avec le vrai SDK Anthropic face à un faux serveur (forme exacte de la requête, refus, troncature, 401). Aucune clé disponible lors du développement. |
| Concept « gabarit » (sans clé) | ✅ Fonctionne | **Non-IA**, déterministe, étiqueté « Gabarit, sans IA » partout. Aucune rédaction créative. |
| Fournisseur **OpenAI Sora** | ⚠️ Écrit, **jamais exécuté en réel** | Contrat vérifié dans les types du SDK officiel `openai`. Texte→vidéo uniquement. Pas d'annulation (l'API n'en a pas). |
| Fournisseur **Replicate** | ⚠️ Écrit, **jamais exécuté en réel** | Transport vérifié dans le code du SDK officiel. Le **mapping des paramètres du modèle** (`seedance-1-lite`) vient de la doc publique, non vérifié en live. |
| Fournisseur **fal.ai** | ⚠️ Écrit, **jamais exécuté en réel** | Transport vérifié dans le code du SDK officiel. Mapping du modèle (`kling-video v2.1 master`) non vérifié en live. |
| Cycle de vie d'une génération (poll → téléchargement → stockage) | ✅ Testé de bout en bout | PostgreSQL + stockage disque + **vrais adaptateurs** face à un faux serveur HTTP : succès, échec fournisseur, HTML au lieu d'une vidéo, fichier vide, retries, concurrence, timeout, quotas. |
| Lecture / téléchargement / seeking de la vidéo | ✅ Vérifié en navigateur | Fichier réel décodé par Chromium, `Range` 206/416, téléchargement identique octet pour octet. |
| Isolation entre utilisateurs, CSRF, SSRF | ✅ Vérifié | Tests + parcours navigateur (IDOR → 404, Origin étrangère → 403, URL de sortie interne refusée). |
| **Montage photo gratuit (sans IA)** | ✅ Fonctionne, sans clé | Anime vos photos (zoom lent, fondus) + nom, avantages, appel à l'action, logo → MP4 muet 1080p. Vérifié avec un vrai ffmpeg (build 2018 embarqué *et* build récent), pixels et durée contrôlés, cycle de vie complet sur PostgreSQL. **Ne génère aucune image ni mouvement** : il met en scène vos propres photos. Vidéo lisible dans Chrome/Edge/Safari/VLC (H.264). |
| Voix off / musique générées, montage multi-clips d'IA | ❌ Non fourni | Un clip par génération ; le script de voix off est fourni pour votre montage. Le montage gratuit est muet. |
| Paiement / crédits | ❌ Non fourni | Garde-fous coût : quota/jour + jobs simultanés. |
| Stockage S3 / R2 | ❌ Non fourni | Interface prête (`StorageDriver`), seul le disque local est livré → volume persistant requis en production. |
| Webhooks fournisseurs | ❌ Non fourni | Suivi par polling (UI) + endpoint cron. |

**Le premier test avec vos propres clés est la vraie validation des trois adaptateurs.** Toute erreur du fournisseur (paramètre refusé, contenu bloqué, quota) s'affiche telle quelle sur la génération concernée ; rien n'est jamais marqué comme réussi sans fichier vidéo reçu.

## Démarrage rapide

Prérequis : Node ≥ 20.19 et PostgreSQL (développé et testé sur Node 22 / PostgreSQL 16).

```bash
cp .env.example .env        # puis renseigner au minimum DATABASE_URL et BETTER_AUTH_SECRET
docker compose up -d db     # optionnel : PostgreSQL local (docker-compose.yml, non exécuté dans l'environnement de développement)
npm install                 # génère aussi le client Prisma
npx prisma migrate deploy   # applique les migrations
npm run dev                 # http://localhost:3000
```

`BETTER_AUTH_SECRET` : `openssl rand -base64 32`. `APP_URL` doit être l'URL exacte utilisée dans le navigateur (origine de confiance de l'auth et du contrôle CSRF) : `localhost` et `127.0.0.1` sont deux origines différentes.

### Brancher un fournisseur vidéo

Renseignez **au moins une** variable puis redémarrez. Le modèle correspondant devient sélectionnable.

| Variable | Fournisseur | Modèles |
| --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI | Sora 2, Sora 2 Pro |
| `REPLICATE_API_TOKEN` | Replicate | Seedance 1 Lite |
| `FAL_KEY` | fal.ai | Kling 2.1 Master |

**Sans aucune clé**, vous pouvez quand même obtenir une vidéo avec le **montage photo gratuit** (nécessite au moins une photo produit ; ffmpeg est installé par `npm install`). Il tourne sur le serveur de l'application, un rendu à la fois (8 en attente au maximum), ne consomme pas le quota payant et est étiqueté « sans IA » dans toute l'interface.

Sans `ANTHROPIC_API_KEY`, le concept est un gabarit non-IA. Modèle par défaut : `claude-opus-5-5` (surchargeable avec `ANTHROPIC_MODEL`, famille Opus/Sonnet 5.x).

La page **Fournisseurs** (`/settings`) affiche l'état réel de la configuration (jamais les clés).

### Production

- `ALLOW_SIGNUP=false` une fois vos comptes créés : une clé fournisseur, c'est de l'argent réel.
- `DAILY_GENERATION_LIMIT` (défaut 5) plafonne les générations par utilisateur et par jour UTC ; 3 jobs simultanés max par utilisateur.
- **Cron obligatoire pour ne pas perdre de vidéos** : sans lui, une vidéo terminée n'est récupérée que quand quelqu'un ouvre le projet, et certains fournisseurs suppriment leurs fichiers après un délai court (parfois ~1 h).
  Définissez `CRON_SECRET` (≥ 16 car.) et appelez chaque minute :
  `curl -H "Authorization: Bearer $CRON_SECRET" https://votre-domaine/api/cron/sync-generations`
- `STORAGE_DIR` doit pointer vers un **volume persistant** (le disque d'un hébergeur serverless est éphémère). Les rendus du montage utilisent un sous-dossier privé `STORAGE_DIR/.montage-tmp` (permissions 0700, nettoyé après chaque vidéo).
- Le montage consomme du **CPU du serveur** (quelques secondes à une minute par vidéo, priorité abaissée). Variables optionnelles : `FFMPEG_PATH` (ffmpeg à utiliser à la place de celui de `npm install`), `MONTAGE_FONT_DIR` (dossier des polices Geist).
- Derrière un reverse proxy, vérifiez que `Origin`/`Host` sont transmis tels quels (contrôle CSRF des Server Actions) ; au besoin, `experimental.serverActions.allowedOrigins` dans `next.config.ts`.
- Pas de CSP stricte livrée (les scripts inline de Next demandent des *nonces*) ; les autres en-têtes de sécurité usuels sont en place.

## Scripts

| Commande | Rôle |
| --- | --- |
| `npm run dev` / `build` / `start` | Next.js |
| `npm run typecheck` / `lint` | TypeScript strict / ESLint |
| `npm test` | Vitest (nécessite PostgreSQL, voir ci-dessous) |
| `npm run db:migrate` | Crée une migration en développement |
| `npm run db:deploy` | Applique les migrations (CI / production) |

### Tests

Les tests d'intégration utilisent une **vraie base PostgreSQL** (`adstudio_test` par défaut, ou `TEST_DATABASE_URL`) et appliquent les migrations eux-mêmes.

```bash
createdb adstudio_test   # ou : TEST_DATABASE_URL=postgresql://… npm test
npm test
```

La CI GitHub (`.github/workflows/ci.yml`) reprend ces étapes ; elle n'a pas pu être exécutée ici.

Les doubles de test (faux serveur HTTP imitant les API fournisseurs) vivent **uniquement** dans `tests/` et ne sont jamais importés par `src/`. Ils prouvent la logique du client, pas le comportement des API réelles.

## Ajouter un fournisseur vidéo

1. Créer `src/modules/video-providers/<nom>.ts` implémentant `VideoProvider` (`submit`, `poll`, `download`, `cancel?`, profils de modèles avec leurs capacités déclarées : durées, formats, image de référence).
2. L'ajouter dans `buildProviders` (`registry.ts`) et ses variables d'environnement dans `src/lib/env.ts` / `.env.example`.
3. Écrire ses tests face à un faux serveur (voir `tests/providers/`).

Règles du contrat : `cleanup?` (optionnel) est appelé une fois le job terminé (stocké, échoué, annulé ou supprimé) ; `submit` n'est **jamais** rejoué automatiquement (un retry pourrait créer un second job facturé) ; `poll` ne renvoie `succeeded` que si une vidéo existe réellement ; les erreurs transitoires (réseau, 5xx) sont levées comme `ProviderApiError` retryable, jamais déguisées en échec.

## Notes

- `npm audit` signale 4 vulnérabilités « high » dans la chaîne **CLI** de Prisma (`prisma` → `@prisma/config` → `deepmerge-ts`, `mysql2`), en `devDependencies`, hors exécution de l'application et sans usage de MySQL. À surveiller lors des prochaines versions de Prisma 7.
- Prisma 7.10 (dernière version stable) est utilisé ; le tag `latest` de npm pointe sur une RC 8.
- Les attentes de l'API Claude respectent les contraintes d'Opus 5.5 (pas de `thinking` désactivé, pas de paramètres d'échantillonnage, `effort` explicite). Le paramètre serveur `fallbacks` n'est **pas** activé : sa combinaison de beta-headers n'a pas pu être vérifiée sans clé, et un refus est de toute façon remonté proprement à l'utilisateur.

## Clipper : chaîne de clips automatisée

Le dossier [`clipper/`](clipper/README.md) contient un outil Python indépendant de l'application : il repère les meilleurs moments des streamers (clips Twitch, pics « les plus revus » sur YouTube), les monte en shorts verticaux sous-titrés et en best-of de la semaine, puis les publie sur YouTube et TikTok depuis GitHub Actions (`.github/workflows/clipper.yml`). Mode d'emploi, état réel et limites : [`clipper/README.md`](clipper/README.md).
