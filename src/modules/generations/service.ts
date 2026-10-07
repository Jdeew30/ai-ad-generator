import { Readable } from "node:stream";
import type { ReadableStream as NodeWebReadableStream } from "node:stream/web";
import type { Generation } from "@/generated/prisma/client";
import type { Db } from "@/lib/db";
import { getEnv } from "@/lib/env";
import { AppError } from "@/lib/errors";
import { prepareReferenceImage } from "@/modules/assets/reference-image";
import { ASPECT_RATIOS } from "@/modules/projects/presets";
import { StorageLimitError, getStorage } from "@/modules/storage";
import {
  MAX_PROMPT_CHARS,
  ProviderApiError,
  checkCompatibility,
  composePrompt,
  findModel,
  getProvider,
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
  if (error instanceof ProviderApiError || error instanceof AppError) return error.message;
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

/**
 * Validates, enforces cost guards, records the job and submits it to the
 * provider. If the provider rejects the request, the returned generation is
 * FAILED with the provider's message (no phantom job is ever left PROCESSING).
 */
export async function startGeneration(db: Db, userId: string, input: StartGenerationInput): Promise<Generation> {
  const project = await db.project.findFirst({
    where: { id: input.projectId, userId },
    include: { concepts: { orderBy: { version: "desc" }, take: 1 } },
  });
  if (!project) throw new AppError("Projet introuvable.", "NOT_FOUND");

  const concept = project.concepts[0];
  if (!concept) throw new AppError("Générez d'abord un concept pour ce projet.");

  const prompt = concept.videoPrompt.trim();
  if (!prompt) throw new AppError("Le prompt vidéo est vide.");
  if (prompt.length > MAX_PROMPT_CHARS) {
    throw new AppError(`Le prompt vidéo dépasse ${MAX_PROMPT_CHARS} caractères (${prompt.length}). Raccourcissez-le.`);
  }

  const entry = findModel(input.modelId);
  if (!entry) throw new AppError("Modèle vidéo inconnu.");
  if (!entry.configured) {
    throw new AppError(
      `${entry.provider.label} n'est pas configuré : définissez ${entry.provider.envVars.join(", ")} sur le serveur.`,
      "NOT_CONFIGURED",
    );
  }

  const aspect = ASPECT_RATIOS.find((a) => a === project.aspectRatio);
  if (!aspect) throw new AppError("Format du projet invalide.");

  let referenceAsset: { id: string; storageKey: string } | null = null;
  if (input.referenceAssetId) {
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
  });
  if (problems.length) throw new AppError(problems.join(" "));

  const env = getEnv();
  const now = new Date();

  // Cost guards. The advisory lock serialises concurrent starts for one user so
  // that two parallel requests cannot both slip under the limits.
  const generation = await db.$transaction(async (tx) => {
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${userId}))`;

    const usedToday = await tx.generation.count({
      where: {
        userId,
        createdAt: { gte: startOfUtcDay(now) },
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
    const active = await tx.generation.count({ where: { userId, status: { in: [...ACTIVE_STATUSES] } } });
    if (active >= MAX_ACTIVE_PER_USER) {
      throw new AppError(`${MAX_ACTIVE_PER_USER} générations sont déjà en cours. Attendez la fin de l'une d'elles.`, "QUOTA");
    }

    return tx.generation.create({
      data: {
        projectId: project.id,
        userId,
        conceptId: concept.id,
        providerId: entry.provider.id,
        modelId: entry.profile.id,
        prompt,
        negativePrompt: concept.negativePrompt,
        aspectRatio: aspect,
        durationSeconds: input.durationSeconds,
        referenceAssetId: referenceAsset?.id ?? null,
        status: "PENDING",
      },
    });
  });

  try {
    let referenceImage: ReferenceImage | undefined;
    if (referenceAsset) {
      const chunks: Buffer[] = [];
      for await (const chunk of getStorage().createReadStream(referenceAsset.storageKey)) chunks.push(chunk as Buffer);
      referenceImage = await prepareReferenceImage(Buffer.concat(chunks));
    }

    const composed = composePrompt(entry.profile, prompt, concept.negativePrompt);
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
    if (!(error instanceof ProviderApiError) && !(error instanceof AppError)) {
      console.error("[generation] submit failed unexpectedly", error);
    }
    return db.generation.update({
      where: { id: generation.id },
      data: {
        status: "FAILED",
        errorMessage: describeError(error, "La soumission au fournisseur a échoué."),
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
  } catch (error) {
    if (storedKey) await storage.delete(storedKey).catch(() => undefined);

    const permanent =
      error instanceof StorageLimitError || (error instanceof ProviderApiError && !error.retryable);
    const attempts = g.downloadAttempts + 1;
    const message = error instanceof StorageLimitError
      ? "La vidéo dépasse la taille maximale acceptée."
      : describeError(error, "Le téléchargement de la vidéo a échoué.");

    if (!(error instanceof ProviderApiError) && !(error instanceof StorageLimitError)) {
      console.error("[generation] download failed", error);
    }

    if (permanent || attempts >= MAX_DOWNLOAD_ATTEMPTS) {
      await failGeneration(db, g.id, message, ["DOWNLOADING"]);
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
      await failGeneration(db, g.id, "La soumission au fournisseur a été interrompue.", ["PENDING"]);
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

  const submittedAt = g.submittedAt ?? g.createdAt;
  if (age(submittedAt) > MAX_JOB_MS) {
    if (provider.cancel) await provider.cancel(job).catch(() => undefined);
    await failGeneration(
      db,
      g.id,
      "Délai dépassé : le fournisseur n'a pas terminé la vidéo en 45 minutes. Le job a été abandonné.",
      ["PROCESSING"],
    );
    return db.generation.findUnique({ where: { id } });
  }

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
      error instanceof AppError ||
      (error instanceof ProviderApiError && (error.retryable || error.status === 401 || error.status === 403));
    if (transient) {
      // Keep the job: it may still finish. The note is shown to the user.
      await db.generation.updateMany({
        where: { id: g.id, status: "PROCESSING" },
        data: { errorMessage: `Suivi temporairement impossible : ${describeError(error, "erreur inconnue")}` },
      });
    } else {
      await failGeneration(db, g.id, describeError(error, "Le suivi du job a échoué."), ["PROCESSING"]);
    }
    return db.generation.findUnique({ where: { id } });
  }

  switch (status.state) {
    case "queued":
      await db.generation.updateMany({ where: { id: g.id, status: "PROCESSING" }, data: { errorMessage: null } });
      break;
    case "processing":
      await db.generation.updateMany({
        where: { id: g.id, status: "PROCESSING" },
        data: { errorMessage: null, ...(status.progress !== undefined ? { progress: status.progress } : {}) },
      });
      break;
    case "failed":
      await failGeneration(db, g.id, status.message, ["PROCESSING"]);
      break;
    case "canceled":
      await db.generation.updateMany({
        where: { id: g.id, status: "PROCESSING" },
        data: { status: "CANCELED", completedAt: now, errorMessage: null },
      });
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
    }),
  );
}

export async function deleteGeneration(db: Db, userId: string, id: string): Promise<void> {
  const g = await getGenerationForUser(db, userId, id);
  if (!isTerminal(g.status)) throw new AppError("Attendez la fin de la génération avant de la supprimer.");
  await db.generation.delete({ where: { id } });
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
