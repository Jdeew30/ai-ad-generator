import { Readable } from "node:stream";
import type { ReadableStream as NodeWebReadableStream } from "node:stream/web";
import type { Generation } from "@/generated/prisma/client";
import type { Db } from "@/lib/db";
import { getEnv } from "@/lib/env";
import { AppError, isAppError } from "@/lib/errors";
import { prepareReferenceImage } from "@/modules/assets/reference-image";
import { planTimeline } from "@/modules/montage/plan";
import type { MontageSpec } from "@/modules/montage/types";
import { ASPECT_RATIOS } from "@/modules/projects/presets";
import { getStorage, isStorageLimitError } from "@/modules/storage";
import {
  MAX_PROMPT_CHARS,
  ProviderApiError,
  checkCompatibility,
  composePrompt,
  findModel,
  getProvider,
  isProviderApiError,
  type JobRef,
  type ProviderId,
  type ReferenceImage,
  type SucceededStatus,
  type VideoProvider,
} from "@/modules/video-providers";
import { MAX_VIDEO_BYTES, detectVideo, videoExtension } from "./video-file";

/**
 * Generation lifecycle (docs/ARCHITECTURE.md §6):
 *
 *   PENDING ─submit ok─▶ PROCESSING ─poll succeeded─▶ DOWNLOADING ─file stored & verified─▶ SUCCEEDED
 *      │ submit error        │ failed/timeout                │ permanent error / retries exhausted
 *      ▼                     ▼                               ▼
 *    FAILED               FAILED                          FAILED          (CANCELED from PENDING/PROCESSING)
 *
 * SUCCEEDED is written in exactly one place (`downloadAndStore`), only after a
 * real video file has been copied to our storage and its container verified.
 */

const POLL_MIN_INTERVAL_MS = 3_000;
const PENDING_STALE_MS = 5 * 60_000;
const DOWNLOADING_STALE_MS = 10 * 60_000;
const MAX_JOB_MS = 45 * 60_000;
const MAX_DOWNLOAD_ATTEMPTS = 3;
const MAX_ACTIVE_PER_USER = 3;
const MIN_VIDEO_BYTES = 1024;

export const ACTIVE_STATUSES = ["PENDING", "PROCESSING", "DOWNLOADING"] as const;
export const TERMINAL_STATUSES = ["SUCCEEDED", "FAILED", "CANCELED"] as const;

const isTerminal = (status: Generation["status"]) => (TERMINAL_STATUSES as readonly string[]).includes(status);

function startOfUtcDay(now: Date): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
}

function describeError(error: unknown, fallback: string): string {
  if (isProviderApiError(error) || isAppError(error)) return error.message;
  return fallback;
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

export interface StartGenerationInput {
  projectId: string;
  modelId: string;
  durationSeconds: number;
  /** Optional product image used as reference (image-to-video). */
  referenceAssetId?: string | null;
}

const MAX_MONTAGE_PHOTOS = 5;

async function readAsset(storageKey: string): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of getStorage().createReadStream(storageKey)) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks);
}

/** Collects the project's own photos, logo and words for the local (non-AI) montage. */
async function buildMontageSpec(
  project: {
    productName: string;
    benefits: string[];
    callToAction: string | null;
    assets: { id: string; kind: "LOGO" | "PRODUCT_IMAGE"; storageKey: string }[];
  },
  aspectRatio: MontageSpec["aspectRatio"],
  durationSeconds: number,
): Promise<MontageSpec> {
  const images: Uint8Array[] = [];
  for (const asset of project.assets.filter((a) => a.kind === "PRODUCT_IMAGE").slice(0, MAX_MONTAGE_PHOTOS)) {
    try {
      images.push((await prepareReferenceImage(await readAsset(asset.storageKey), 1600)).bytes);
    } catch (error) {
      console.warn("[montage] skipping unreadable photo", asset.id, error);
    }
  }
  if (images.length === 0) throw new AppError("Aucune photo produit exploitable : ajoutez-en une dans le projet.");

  let logo: Uint8Array | undefined;
  const logoAsset = project.assets.find((a) => a.kind === "LOGO");
  if (logoAsset) {
    try {
      logo = (await prepareReferenceImage(await readAsset(logoAsset.storageKey), 800)).bytes;
    } catch (error) {
      console.warn("[montage] skipping unreadable logo", error); // the logo is optional
    }
  }

  return {
    aspectRatio,
    durationSeconds,
    images,
    logo,
    headline: project.productName,
    captions: project.benefits.slice(0, 3),
    callToAction: project.callToAction ?? undefined,
  };
}

/** Stored description: derived from the plan that is actually rendered, so it never promises more than the video shows. */
function describeMontage(spec: MontageSpec): string {
  const plan = planTimeline(spec);
  const photosUsed = new Set(plan.map((scene) => scene.imageIndex)).size;
  const texts = plan.flatMap((scene) => scene.texts.map((t) => `« ${t.text} »`));
  const logo = !spec.logo ? "pas de logo" : plan.some((scene) => scene.isEndCard) ? "logo affiché sur la dernière scène" : "logo non affiché";
  return [
    "Montage automatique (sans IA, réalisé sur le serveur) :",
    `${plan.length} scène(s), ${spec.durationSeconds} s, ${spec.aspectRatio} ; ${photosUsed} photo(s) utilisée(s) sur ${spec.images.length} ; ${logo}.`,
    texts.length ? `Textes affichés : ${texts.join(" ; ")}.` : "",
    "Les textes trop longs sont raccourcis et les symboles non pris en charge (émojis…) sont retirés.",
  ]
    .filter(Boolean)
    .join(" ");
}

/**
 * Validates, enforces cost guards, records the job and submits it to the
 * provider. If the provider rejects the request, the returned generation is
 * FAILED with the provider's message (no phantom job is ever left PROCESSING).
 *
 * AI models are driven by the concept's video prompt. The local montage model is
 * not AI: it needs no concept, uses the project's own photos and brief text, and is
 * free, so it is exempt from the daily paid-generation quota.
 */
export async function startGeneration(db: Db, userId: string, input: StartGenerationInput): Promise<Generation> {
  const project = await db.project.findFirst({
    where: { id: input.projectId, userId },
    include: {
      concepts: { orderBy: { version: "desc" }, take: 1 },
      assets: { orderBy: { createdAt: "asc" }, select: { id: true, kind: true, storageKey: true } },
    },
  });
  if (!project) throw new AppError("Projet introuvable.", "NOT_FOUND");

  const entry = findModel(input.modelId);
  if (!entry) throw new AppError("Modèle vidéo inconnu.");
  if (!entry.configured) {
    throw new AppError(
      `${entry.provider.label} n'est pas configuré : définissez ${entry.provider.envVars.join(", ")} sur le serveur.`,
      "NOT_CONFIGURED",
    );
  }
  const isMontage = entry.profile.capabilities.montage === true;

  const concept = project.concepts[0];
  let prompt = "";
  if (!isMontage) {
    if (!concept) throw new AppError("Générez d'abord un concept pour ce projet.");
    prompt = concept.videoPrompt.trim();
    if (!prompt) throw new AppError("Le prompt vidéo est vide.");
    if (prompt.length > MAX_PROMPT_CHARS) {
      throw new AppError(`Le prompt vidéo dépasse ${MAX_PROMPT_CHARS} caractères (${prompt.length}). Raccourcissez-le.`);
    }
  }

  const aspect = ASPECT_RATIOS.find((a) => a === project.aspectRatio);
  if (!aspect) throw new AppError("Format du projet invalide.");

  let referenceAsset: { id: string; storageKey: string } | null = null;
  if (input.referenceAssetId && !isMontage) {
    referenceAsset = await db.asset.findFirst({
      where: { id: input.referenceAssetId, projectId: project.id, kind: "PRODUCT_IMAGE" },
      select: { id: true, storageKey: true },
    });
    if (!referenceAsset) throw new AppError("Image de référence introuvable.");
  }

  const problems = checkCompatibility(entry.profile, {
    aspectRatio: aspect,
    durationSeconds: input.durationSeconds,
    withImage: Boolean(referenceAsset),
    productImages: project.assets.filter((a) => a.kind === "PRODUCT_IMAGE").length,
  });
  if (problems.length) throw new AppError(problems.join(" "));

  const env = getEnv();
  const now = new Date();

  // Cost guards. The advisory lock serialises concurrent starts for one user so
  // that two parallel requests cannot both slip under the limits.
  const generation = await db.$transaction(async (tx) => {
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${userId}))`;

    if (!isMontage) {
      const usedToday = await tx.generation.count({
        where: {
          userId,
          createdAt: { gte: startOfUtcDay(now) },
          providerId: { not: "montage" },
          // A job that never reached the provider cost nothing.
          NOT: { externalId: null, status: { in: ["FAILED", "CANCELED"] } },
        },
      });
      if (usedToday >= env.DAILY_GENERATION_LIMIT) {
        throw new AppError(
          `Limite quotidienne atteinte (${env.DAILY_GENERATION_LIMIT} générations par jour). Réessayez demain.`,
          "QUOTA",
        );
      }
    }
    const active = await tx.generation.count({ where: { userId, status: { in: [...ACTIVE_STATUSES] } } });
    if (active >= MAX_ACTIVE_PER_USER) {
      throw new AppError(`${MAX_ACTIVE_PER_USER} générations sont déjà en cours. Attendez la fin de l'une d'elles.`, "QUOTA");
    }

    return tx.generation.create({
      data: {
        projectId: project.id,
        userId,
        conceptId: isMontage ? null : (concept?.id ?? null),
        providerId: entry.provider.id,
        modelId: entry.profile.id,
        prompt: prompt || "Montage automatique (sans IA).",
        negativePrompt: isMontage ? null : (concept?.negativePrompt ?? null),
        aspectRatio: aspect,
        durationSeconds: input.durationSeconds,
        referenceAssetId: referenceAsset?.id ?? null,
        status: "PENDING",
      },
    });
  });

  try {
    if (isMontage) {
      const montage = await buildMontageSpec(project, aspect, input.durationSeconds);
      await db.generation.update({ where: { id: generation.id }, data: { prompt: describeMontage(montage) } });
      const submitted = await entry.provider.submit({
        model: entry.profile,
        prompt: "",
        aspectRatio: aspect,
        durationSeconds: input.durationSeconds,
        montage,
      });
      return await db.generation.update({
        where: { id: generation.id },
        data: { status: "PROCESSING", externalId: submitted.externalId, submittedAt: new Date() },
      });
    }

    let referenceImage: ReferenceImage | undefined;
    if (referenceAsset) {
      referenceImage = await prepareReferenceImage(await readAsset(referenceAsset.storageKey));
    }

    const composed = composePrompt(entry.profile, prompt, concept?.negativePrompt);
    const submitted = await entry.provider.submit({
      model: entry.profile,
      prompt: composed.prompt,
      negativePrompt: composed.negativePrompt,
      aspectRatio: aspect,
      durationSeconds: input.durationSeconds,
      referenceImage,
    });

    return await db.generation.update({
      where: { id: generation.id },
      data: {
        status: "PROCESSING",
        externalId: submitted.externalId,
        providerData: (submitted.providerData ?? undefined) as object | undefined,
        submittedAt: new Date(),
      },
    });
  } catch (error) {
    if (!isProviderApiError(error) && !isAppError(error)) {
      console.error("[generation] submit failed unexpectedly", error);
    }
    return db.generation.update({
      where: { id: generation.id },
      data: {
        status: "FAILED",
        errorMessage: describeError(error, isMontage ? "Le montage a échoué." : "La soumission au fournisseur a échoué."),
        completedAt: new Date(),
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Sync (poll → download)
// ---------------------------------------------------------------------------

function jobRef(g: Generation): JobRef | null {
  if (!g.externalId) return null;
  return {
    externalId: g.externalId,
    modelId: g.modelId,
    providerData: (g.providerData as Record<string, unknown> | null) ?? null,
  };
}

/** Reads the first `n` bytes and returns a stream that still yields everything. */
async function readHead(source: Readable, n: number): Promise<{ head: Buffer; stream: Readable }> {
  const iterator = source[Symbol.asyncIterator]();
  const buffered: Buffer[] = [];
  let length = 0;
  let ended = false;
  while (length < n) {
    const next = await iterator.next();
    if (next.done) {
      ended = true;
      break;
    }
    const chunk = Buffer.from(next.value as Uint8Array);
    buffered.push(chunk);
    length += chunk.length;
  }
  const head = Buffer.concat(buffered);
  async function* replay() {
    if (head.length) yield head;
    if (ended) return;
    for (;;) {
      const next = await iterator.next();
      if (next.done) return;
      yield Buffer.from(next.value as Uint8Array);
    }
  }
  return { head, stream: Readable.from(replay()) };
}

/** Best effort: lets a provider that keeps local files (the montage) remove them once a job is over. */
async function releaseJob(g: Generation): Promise<void> {
  const provider = getProvider(g.providerId as ProviderId);
  const job = jobRef(g);
  if (provider?.cleanup && job) await provider.cleanup(job).catch(() => undefined);
}

async function failGeneration(db: Db, id: string, message: string, from: Generation["status"][]) {
  await db.generation.updateMany({
    where: { id, status: { in: from } },
    data: { status: "FAILED", errorMessage: message, completedAt: new Date() },
  });
}

async function downloadAndStore(
  db: Db,
  g: Generation,
  provider: VideoProvider,
  job: JobRef,
  status: SucceededStatus,
): Promise<void> {
  const storage = getStorage();
  let storedKey: string | undefined;
  try {
    const res = await provider.download(job, status);
    const declared = Number(res.headers.get("content-length") ?? 0);
    if (declared > MAX_VIDEO_BYTES) {
      throw new ProviderApiError(provider.label, 0, "La vidéo dépasse la taille maximale acceptée.", false);
    }
    if (!res.body) throw new ProviderApiError(provider.label, 0, "Réponse de téléchargement vide.", false);

    const { head, stream } = await readHead(Readable.fromWeb(res.body as unknown as NodeWebReadableStream), 16);
    const detected = detectVideo(head);
    if (!detected) {
      stream.destroy();
      throw new ProviderApiError(provider.label, 0, "Le fichier reçu du fournisseur n'est pas une vidéo valide.", false);
    }

    const key = `videos/${g.userId}/${g.id}.${videoExtension(detected.mime)}`;
    const { size } = await storage.put(key, stream, { maxBytes: MAX_VIDEO_BYTES });
    storedKey = key;
    if (size < MIN_VIDEO_BYTES) {
      throw new ProviderApiError(provider.label, 0, "Le fichier vidéo reçu est vide ou tronqué.", false);
    }
    // Compare against the announced length when the body was not transfer-encoded.
    if (declared > 0 && !res.headers.get("content-encoding") && size !== declared) {
      throw new ProviderApiError(provider.label, 0, "Téléchargement incomplet (taille reçue différente de la taille annoncée).", true);
    }

    await db.generation.update({
      where: { id: g.id },
      data: {
        status: "SUCCEEDED",
        outputKey: key,
        outputBytes: size,
        outputMimeType: detected.mime,
        progress: 100,
        errorMessage: null,
        completedAt: new Date(),
      },
    });
    await releaseJob(g);
  } catch (error) {
    if (storedKey) await storage.delete(storedKey).catch(() => undefined);

    const permanent =
      isStorageLimitError(error) || (isProviderApiError(error) && !error.retryable);
    const attempts = g.downloadAttempts + 1;
    const message = isStorageLimitError(error)
      ? "La vidéo dépasse la taille maximale acceptée."
      : describeError(error, "Le téléchargement de la vidéo a échoué.");

    if (!isProviderApiError(error) && !isStorageLimitError(error)) {
      console.error("[generation] download failed", error);
    }

    if (permanent || attempts >= MAX_DOWNLOAD_ATTEMPTS) {
      await failGeneration(db, g.id, message, ["DOWNLOADING"]);
      await releaseJob(g);
    } else {
      // Transient: the job is paid for, so go back to PROCESSING and retry on the next sync.
      await db.generation.updateMany({
        where: { id: g.id, status: "DOWNLOADING" },
        data: { status: "PROCESSING", downloadAttempts: attempts, errorMessage: `${message} Nouvelle tentative…`, lastPolledAt: null },
      });
    }
  }
}

export interface SyncOptions {
  /** Ignore the per-generation poll throttle (used by tests and the cron). */
  force?: boolean;
  now?: Date;
}

/**
 * Advances one generation as far as the provider's current state allows.
 * Idempotent and safe to call concurrently: transitions are guarded by
 * conditional updates so only one caller downloads a given file.
 */
export async function syncGeneration(db: Db, id: string, options: SyncOptions = {}): Promise<Generation | null> {
  const now = options.now ?? new Date();
  let g = await db.generation.findUnique({ where: { id } });
  if (!g || isTerminal(g.status)) return g;

  const age = (since: Date) => now.getTime() - since.getTime();

  if (g.status === "PENDING") {
    if (age(g.createdAt) > PENDING_STALE_MS) {
      await failGeneration(db, g.id, "Le démarrage de la génération a été interrompu.", ["PENDING"]);
    }
    return db.generation.findUnique({ where: { id } });
  }

  if (g.status === "DOWNLOADING") {
    if (age(g.updatedAt) > DOWNLOADING_STALE_MS) {
      // A previous worker died mid-download: hand the job back for another attempt.
      await db.generation.updateMany({
        where: { id: g.id, status: "DOWNLOADING", updatedAt: g.updatedAt },
        data: { status: "PROCESSING", lastPolledAt: null },
      });
      g = (await db.generation.findUnique({ where: { id } })) ?? g;
    } else {
      return g;
    }
  }

  // --- PROCESSING ---
  const provider = getProvider(g.providerId as ProviderId);
  const job = jobRef(g);
  if (!provider || !job) {
    await failGeneration(db, g.id, "Fournisseur ou identifiant de job introuvable.", ["PROCESSING"]);
    return db.generation.findUnique({ where: { id } });
  }

  // The age limit only applies to jobs that are still unfinished: a result that is ready
  // always wins, even if it is collected late.
  const expired = age(g.submittedAt ?? g.createdAt) > MAX_JOB_MS;
  const expire = async () => {
    if (provider.cancel) await provider.cancel(job).catch(() => undefined);
    await failGeneration(
      db,
      g.id,
      provider.id === "montage"
        ? "Délai dépassé : le montage n'a pas abouti en 45 minutes. Il a été abandonné."
        : "Délai dépassé : le fournisseur n'a pas terminé la vidéo en 45 minutes. Le job a été abandonné.",
      ["PROCESSING"],
    );
    await releaseJob(g);
  };

  // Claim this poll (throttle + dedupe across tabs/cron).
  if (!options.force) {
    const claimed = await db.generation.updateMany({
      where: {
        id: g.id,
        status: "PROCESSING",
        OR: [{ lastPolledAt: null }, { lastPolledAt: { lt: new Date(now.getTime() - POLL_MIN_INTERVAL_MS) } }],
      },
      data: { lastPolledAt: now },
    });
    if (claimed.count === 0) return db.generation.findUnique({ where: { id } });
  } else {
    await db.generation.updateMany({ where: { id: g.id, status: "PROCESSING" }, data: { lastPolledAt: now } });
  }

  let status;
  try {
    status = await provider.poll(job);
  } catch (error) {
    const transient =
      isAppError(error) ||
      (isProviderApiError(error) && (error.retryable || error.status === 401 || error.status === 403));
    if (transient && expired) {
      await expire();
    } else if (transient) {
      // Keep the job: it may still finish. The note is shown to the user.
      await db.generation.updateMany({
        where: { id: g.id, status: "PROCESSING" },
        data: { errorMessage: `Suivi temporairement impossible : ${describeError(error, "erreur inconnue")}` },
      });
    } else {
      await failGeneration(db, g.id, describeError(error, "Le suivi du job a échoué."), ["PROCESSING"]);
      await releaseJob(g);
    }
    return db.generation.findUnique({ where: { id } });
  }

  switch (status.state) {
    case "queued":
      if (expired) await expire();
      else await db.generation.updateMany({ where: { id: g.id, status: "PROCESSING" }, data: { errorMessage: null } });
      break;
    case "processing":
      if (expired) await expire();
      else {
        await db.generation.updateMany({
          where: { id: g.id, status: "PROCESSING" },
          data: { errorMessage: null, ...(status.progress !== undefined ? { progress: status.progress } : {}) },
        });
      }
      break;
    case "failed":
      await failGeneration(db, g.id, status.message, ["PROCESSING"]);
      await releaseJob(g);
      break;
    case "canceled":
      await db.generation.updateMany({
        where: { id: g.id, status: "PROCESSING" },
        data: { status: "CANCELED", completedAt: now, errorMessage: null },
      });
      await releaseJob(g);
      break;
    case "succeeded": {
      const claimed = await db.generation.updateMany({
        where: { id: g.id, status: "PROCESSING" },
        data: { status: "DOWNLOADING" },
      });
      if (claimed.count === 1) {
        await downloadAndStore(db, g, provider, job, status);
      }
      break;
    }
  }
  return db.generation.findUnique({ where: { id } });
}

/** Cron entry point: advances every in-flight generation (oldest first). */
export async function syncActiveGenerations(db: Db, limit = 25): Promise<{ checked: number; finished: number }> {
  const active = await db.generation.findMany({
    where: { status: { in: [...ACTIVE_STATUSES] } },
    orderBy: { updatedAt: "asc" },
    take: limit,
    select: { id: true },
  });
  let finished = 0;
  for (const { id } of active) {
    try {
      const after = await syncGeneration(db, id);
      if (after && isTerminal(after.status)) finished += 1;
    } catch (error) {
      console.error("[cron] sync failed for generation", id, error);
    }
  }
  return { checked: active.length, finished };
}

// ---------------------------------------------------------------------------
// Queries & user actions (always scoped by owner)
// ---------------------------------------------------------------------------

/** Daily usage as counted by the quota guard (jobs that never reached a provider are free). */
export async function getQuotaUsage(db: Db, userId: string, now = new Date()) {
  const used = await db.generation.count({
    where: {
      userId,
      createdAt: { gte: startOfUtcDay(now) },
      providerId: { not: "montage" },
      NOT: { externalId: null, status: { in: ["FAILED", "CANCELED"] } },
    },
  });
  return { used, limit: getEnv().DAILY_GENERATION_LIMIT };
}

export async function getGenerationForUser(db: Db, userId: string, id: string): Promise<Generation> {
  const g = await db.generation.findFirst({ where: { id, userId } });
  if (!g) throw new AppError("Génération introuvable.", "NOT_FOUND");
  return g;
}

export async function listGenerations(db: Db, userId: string, projectId: string): Promise<Generation[]> {
  return db.generation.findMany({
    where: { userId, projectId },
    orderBy: { createdAt: "desc" },
    take: 50,
  });
}

export async function cancelGeneration(db: Db, userId: string, id: string): Promise<Generation> {
  const g = await getGenerationForUser(db, userId, id);
  if (g.status === "PENDING") {
    await db.generation.updateMany({ where: { id, status: "PENDING" }, data: { status: "CANCELED", completedAt: new Date() } });
    return getGenerationForUser(db, userId, id);
  }
  if (g.status !== "PROCESSING") throw new AppError("Cette génération ne peut plus être annulée.");

  const provider = getProvider(g.providerId as ProviderId);
  const job = jobRef(g);
  if (!provider?.cancel || !job) {
    throw new AppError("Ce fournisseur ne permet pas d'annuler un job en cours.");
  }
  try {
    await provider.cancel(job);
  } catch (error) {
    throw new AppError(`Annulation refusée par le fournisseur : ${describeError(error, "erreur inconnue")}`, "UPSTREAM");
  }
  await db.generation.updateMany({
    where: { id, status: "PROCESSING" },
    data: { status: "CANCELED", completedAt: new Date(), errorMessage: null },
  });
  await releaseJob(g);
  return getGenerationForUser(db, userId, id);
}

/**
 * Best-effort: asks each provider to stop in-flight jobs of a project that is
 * about to be deleted, so we do not keep paying for renders nobody can see.
 */
export async function abortActiveGenerations(db: Db, userId: string, projectId: string): Promise<void> {
  const running = await db.generation.findMany({ where: { userId, projectId, status: "PROCESSING" } });
  await Promise.allSettled(
    running.map(async (g) => {
      const provider = getProvider(g.providerId as ProviderId);
      const job = jobRef(g);
      if (provider?.cancel && job) await provider.cancel(job);
      await releaseJob(g);
    }),
  );
}

export async function deleteGeneration(db: Db, userId: string, id: string): Promise<void> {
  const g = await getGenerationForUser(db, userId, id);
  if (!isTerminal(g.status)) throw new AppError("Attendez la fin de la génération avant de la supprimer.");
  await db.generation.delete({ where: { id } });
  await releaseJob(g);
  if (g.outputKey) await getStorage().delete(g.outputKey).catch(() => undefined);
}

/** Returns the stored file info only for a verified, successful generation. */
export async function getDownloadableVideo(db: Db, userId: string, id: string) {
  const g = await getGenerationForUser(db, userId, id);
  if (g.status !== "SUCCEEDED" || !g.outputKey || !g.outputMimeType) {
    throw new AppError("Aucune vidéo disponible pour cette génération.", "NOT_FOUND");
  }
  const stat = await getStorage().stat(g.outputKey);
  if (!stat || stat.size === 0) {
    throw new AppError("Le fichier vidéo est introuvable dans le stockage.", "NOT_FOUND");
  }
  return { generation: g, key: g.outputKey, size: stat.size, mimeType: g.outputMimeType };
}
