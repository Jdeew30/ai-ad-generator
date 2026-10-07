# AI Ad Studio — Architecture

> Statut : le repository était vide au démarrage (aucun commit). Ce document fixe
> les décisions d'architecture **avant** l'implémentation. Il est mis à jour au fil
> des jalons ; la section « État réel » du `README.md` est la source de vérité sur
> ce qui fonctionne vraiment.

## 1. Principe directeur : zéro simulation

Une génération vidéo n'existe que si un fournisseur réel l'a produite.

- Aucun fournisseur « mock » n'est expédié dans l'application. Les doubles de test
  (faux serveur HTTP) vivent exclusivement dans `tests/` et ne sont jamais
  sélectionnables à l'exécution.
- Un fournisseur n'apparaît comme utilisable que si sa clé API est présente dans
  l'environnement. Sinon l'UI affiche l'état « non configuré », la variable
  d'environnement à définir, et le bouton « Générer » reste désactivé.
- Un bouton « Télécharger » n'existe que lorsqu'un fichier vidéo a réellement été
  récupéré chez le fournisseur **et** écrit dans notre stockage (taille > 0, type
  vérifié).
- Les échecs (clé invalide, contenu refusé, timeout, quota) sont enregistrés et
  affichés tels quels. Jamais de « succès » par défaut.
- Le « concept publicitaire » est produit par un LLM (Claude) quand
  `ANTHROPIC_API_KEY` est défini. Sans clé, un gabarit **déterministe et non-IA**
  compile le brief en prompt, et l'UI le dit explicitement (badge « Gabarit, sans IA »).

## 2. Stack

| Couche | Choix | Raison |
| --- | --- | --- |
| Framework | Next.js 16 (App Router), React 19 | Server Components + Server Actions ; une seule base de code front/back |
| Langage | TypeScript strict | Contrats explicites entre modules |
| Style | Tailwind CSS v4 + petit kit UI maison | Pas de dépendance lourde, thème sombre premium cohérent |
| DB | PostgreSQL + Prisma 7 (adapter `pg`) | Relationnel, JSONB pour payloads de concept/fournisseur |
| Auth | Better Auth (email + mot de passe, sessions en base) | Bibliothèque maintenue, cookies httpOnly, hash scrypt, rate limiting intégré. Évite d'écrire de la crypto d'auth maison. Auth.js v5 est toujours en bêta. |
| Validation | Zod 4 | Mêmes schémas côté client et serveur, et pour les sorties LLM |
| LLM | `@anthropic-ai/sdk` | Concept structuré (sortie JSON contrainte par schéma) |
| Vidéo | `fetch` natif derrière l'interface `VideoProvider` | Pas de SDK par fournisseur : surface réduite, testable avec un `fetch` injecté |
| Tests | Vitest (+ Postgres réel pour l'intégration) | La machine d'états est testée contre une vraie DB |

## 3. Découpage modulaire

```
src/
  app/                         # routing uniquement (pages minces, route handlers)
    (marketing)/               # landing
    (auth)/                    # login / register
    (app)/                     # zone authentifiée : dashboard, projects, settings
    api/
      auth/[...all]/           # Better Auth
      generations/[id]/        # GET statut (déclenche une synchro) ; /video = stream/téléchargement
      assets/[id]/             # GET image (propriétaire uniquement)
      cron/sync-generations/   # synchro des jobs en cours (protégé par CRON_SECRET)
  modules/                     # logique métier, sans dépendance à React
    projects/                  # schéma de brief (zod), service, actions
    assets/                    # validation d'upload (magic bytes), service
    concepts/                  # ConceptGenerator : Claude | gabarit ; schéma de concept
    generations/               # machine d'états, quotas, synchro, téléchargement
    video-providers/           # interface, registre, adapters (openai, replicate, fal)
    storage/                   # interface StorageDriver + driver disque
  components/                  # UI (ui/ = primitives, features/ = écrans)
  lib/                         # db, env, auth, erreurs, utilitaires
prisma/schema.prisma
tests/                         # unit + intégration + doubles de test (jamais importés par src/)
```

Règle de dépendance : `app → modules → lib`. Les `modules` ne s'importent entre eux
que dans le sens `generations → video-providers/storage/concepts`. Toute requête DB
passe par un service qui prend un `userId` : **aucune requête non scopée par
propriétaire** (pas d'IDOR).

## 4. Modèle de données

```
User ─┬─< Project ─┬─< Asset            (logo / image produit ; fichier dans le stockage)
      │            ├─< Concept          (versionné ; payload JSON ; source = CLAUDE | TEMPLATE)
      │            └─< Generation       (un job fournisseur ; statut ; fichier de sortie)
      ├─< Session / Account / Verification   (Better Auth)
```

`Project` porte le brief : produit, description, bénéfices, cible, ton, style visuel,
format (9:16 / 16:9 / 1:1), durée cible, langue, CTA.

`Generation` est un **snapshot immuable** de ce qui a été envoyé (prompt, modèle,
durée, format) + l'état du job : `externalId`, `providerData` (URLs de statut
fournisseur), `status`, `error`, `outputKey`, `outputBytes`, `lastPolledAt`.

## 5. Abstraction des fournisseurs vidéo

Toutes les API de génération vidéo sont **asynchrones** (soumission → attente →
récupération), mais diffèrent par l'auth, le format d'entrée, le polling et la façon
de livrer le fichier. L'interface isole ces différences :

```ts
interface VideoProvider {
  id: ProviderId;                      // "openai" | "replicate" | "fal"
  label: string;
  envVars: string[];                   // ex. ["OPENAI_API_KEY"] — affiché dans l'UI
  isConfigured(): boolean;
  models: VideoModelProfile[];         // capacités déclarées : durées, formats, image→vidéo
  submit(input: SubmitInput): Promise<SubmitResult>;       // → externalId + providerData
  poll(job: JobRef): Promise<ProviderJobStatus>;           // queued | processing | succeeded | failed | canceled
  download(job: JobRef): Promise<Response>;                // flux binaire du MP4 (auth gérée par l'adapter)
  cancel?(job: JobRef): Promise<void>;
}
```

- `VideoModelProfile` = capacités **déclaratives** d'un modèle (durées supportées,
  formats supportés, image de référence oui/non, taille de prompt max). Le
  filtrage de l'UI et la validation serveur s'appuient dessus : on n'envoie jamais
  à un modèle une combinaison qu'il ne supporte pas.
- `registry.ts` expose `getConfiguredProviders()` ; ajouter un fournisseur = un
  fichier adapter + une ligne dans le registre.
- Adapters livrés dans le MVP — contrats vérifiés dans le code source des SDK
  officiels, **jamais exécutés contre l'API live** faute de clés dans l'environnement
  de build :
  - **OpenAI** (`/v1/videos`, Sora) — création multipart, polling, contenu authentifié.
  - **Replicate** (`/v1/models/{owner}/{name}/predictions`) — hub multi-modèles.
  - **fal.ai** (`queue.fal.run`) — hub multi-modèles ; `status_url` / `response_url`
    renvoyés à la soumission et persistés (le chemin de statut diffère de celui de
    soumission) ; `COMPLETED` ≠ succès, le résultat est récupéré puis validé.
- Prochains adapters naturels : Google Veo (Gemini API), Runway, Luma.

## 6. Cycle de vie d'une génération

```
PENDING ──submit ok──▶ PROCESSING ──poll succeeded──▶ DOWNLOADING ──fichier écrit──▶ SUCCEEDED
   │ submit error          │ poll failed / timeout          │ download error
   ▼                       ▼                                ▼
 FAILED                  FAILED                           FAILED        (CANCELED depuis PENDING/PROCESSING)
```

- **Pas de worker** dans le MVP (déploiement serverless possible). La synchro est
  idempotente et déclenchée par (a) le polling de l'UI sur `GET /api/generations/:id`,
  (b) `GET /api/cron/sync-generations` appelé par un cron (Bearer `CRON_SECRET`).
- Concurrence : le passage `PROCESSING → DOWNLOADING` est un
  `updateMany ... where status = PROCESSING` ; un seul appelant gagne et télécharge.
  Un `DOWNLOADING` bloqué depuis > 10 min est repris.
- Les URLs de sortie des fournisseurs expirent (ex. Replicate ≈ 1 h) : le fichier est
  **copié dans notre stockage dès le succès** ; l'app ne dépend jamais d'une URL
  fournisseur pour servir ou télécharger.
- Anti-hammering : `lastPolledAt` limite les appels fournisseur à 1 / 3 s / génération.
- Garde-fous coût : quota quotidien par utilisateur (`DAILY_GENERATION_LIMIT`),
  inscription désactivable (`ALLOW_SIGNUP=false`) — une clé fournisseur est de
  l'argent réel.

## 7. Génération du concept

`ConceptGenerator` a deux implémentations :

1. **Claude** (si `ANTHROPIC_API_KEY`) — sortie structurée validée par Zod : accroche,
   scènes minutées (visuel, caméra, texte à l'écran, voix off), script voix off, CTA,
   ambiance musicale, **prompt vidéo consolidé** et prompt négatif. Les images produit
   sont passées en vision pour que le prompt décrive le vrai produit.
2. **Gabarit** (sans clé) — compilation déterministe du brief. Étiqueté « sans IA ».

Le prompt vidéo est **éditable** par l'utilisateur avant génération : c'est le levier
qualité principal, et ce qui est envoyé est exactement ce qui est affiché.

## 8. Stockage

`StorageDriver { put, getStream(range), stat, delete }`. Le MVP livre un driver
disque (`STORAGE_DIR`). Les fichiers sont servis uniquement via des route handlers
authentifiés (avec support `Range` pour le seeking vidéo). Les clés sont générées côté
serveur (jamais dérivées d'un nom de fichier utilisateur → pas de path traversal).
Un driver S3/R2 est le prochain pas pour un déploiement serverless (le disque local
n'y est pas persistant) ; l'interface est prête, **le driver n'est pas livré**.

## 9. Sécurité (points structurants)

- Uploads : taille max, nombre max, **détection par magic bytes** (PNG/JPEG/WebP),
  SVG refusé (vecteur XSS), nom d'origine jamais utilisé comme chemin.
- Autorisation : chaque lecture/écriture est filtrée par `userId`.
- Secrets : clés fournisseurs uniquement côté serveur ; jamais persistées en base ni
  renvoyées au client (l'écran Paramètres n'affiche que « configuré / non configuré »).
- SSRF : les URLs de sortie fournisseur sont limitées à `https:` et aux hôtes publics.
- Téléchargement vidéo : plafond de taille, écriture en flux (pas de buffer mémoire).

## 10. Hors périmètre du MVP (explicite)

Paiement / crédits, e-mail de vérification et reset de mot de passe (aucun service
e-mail branché), montage multi-clips + audio (ffmpeg), webhooks fournisseurs
(signatures à vérifier), driver S3, file d'attente dédiée. Chacun a un point
d'extension identifié ci-dessus.
