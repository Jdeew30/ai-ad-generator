import { randomUUID } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { lstat, mkdir, readdir, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import { AppError } from "@/lib/errors";
import { resolveFfmpeg } from "@/modules/montage/ffmpeg";
import { isMontageError, renderMontage } from "@/modules/montage/render";
import type { MontageSpec } from "@/modules/montage/types";
import { ProviderApiError } from "./http";
import type {
  JobRef,
  ProviderJobStatus,
  SubmitInput,
  SubmitResult,
  SucceededStatus,
  VideoModelProfile,
  VideoProvider,
} from "./types";

/**
 * Local photo montage, exposed through the same VideoProvider contract as the AI
 * providers so it shares the whole job lifecycle (tracking, cancel, download,
 * storage). It is NOT generative AI: it animates the user's photos and typesets the
 * brief's text with ffmpeg on the machine running the server. It is free, and always
 * presented as such.
 *
 * Jobs run one at a time (a render saturates the CPU) at a lowered priority, and the
 * queue is bounded. State lives in memory for the running process; the finished file
 * lives on disk (published atomically as result.mp4), so a finished job survives a
 * restart while an interrupted one is reported as interrupted (never as success).
 * Job folders are private (0700) and removed once the video has been stored.
 */

const MODEL: VideoModelProfile = {
  id: "montage:photo-slideshow",
  providerId: "montage",
  label: "Montage photo (gratuit)",
  description:
    "Anime vos photos produit (zoom lent, fondus) avec le nom, les avantages et l'appel à l'action de votre brief. Vidéo MP4 muette, réalisée sur le serveur de l'application. Ce n'est pas de l'IA.",
  capabilities: {
    durations: [4, 5, 6, 8, 10, 12],
    aspectRatios: ["9:16", "16:9", "1:1"],
    imageToVideo: false,
    supportsNegativePrompt: false,
    montage: true,
  },
};

const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const KEEP_MS = 3 * 60 * 60_000;
const MAX_PENDING_JOBS = 8;
const PRUNE_EVERY_MS = 30 * 60_000;

type JobState = "queued" | "processing" | "done" | "failed" | "canceled";

interface LocalJob {
  state: JobState;
  progress: number;
  message?: string;
  startedAt: number;
  controller: AbortController;
  /** Photos and logo held in memory until the render starts; released on cancel and when done. */
  spec: MontageSpec | null;
}

export interface MontageProviderOptions {
  /** Private folder holding one sub-folder per job. Defaults to a per-user folder in the OS temp dir. */
  workRoot?: string;
  /** Test seam. */
  ffmpegPath?: () => string | null;
}

/** The work folder must be a real directory, not a symlink, and owned by the current user. */
async function ensurePrivateRoot(root: string): Promise<void> {
  await mkdir(root, { recursive: true, mode: 0o700 });
  const info = await lstat(root);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new AppError("Le dossier de travail du montage n'est pas sûr.");
  if (typeof process.getuid === "function" && info.uid !== process.getuid()) {
    throw new AppError("Le dossier de travail du montage appartient à un autre utilisateur du système.");
  }
}

export function createMontageProvider(options: MontageProviderOptions = {}): VideoProvider {
  const workRoot = options.workRoot ?? path.join(os.tmpdir(), `adstudio-montage-${typeof process.getuid === "function" ? process.getuid() : "user"}`);
  const findFfmpeg = options.ffmpegPath ?? resolveFfmpeg;
  const jobs = new Map<string, LocalJob>();
  let queue: Promise<void> = Promise.resolve();
  let pruneTimer: ReturnType<typeof setInterval> | undefined;

  const jobDir = (id: string) => path.join(workRoot, id);
  const resultPath = (id: string) => path.join(jobDir(id), "result.mp4");

  function checkedId(externalId: string): string {
    if (!JOB_ID.test(externalId)) throw new ProviderApiError("Montage", 0, "Identifiant de montage invalide.", false);
    return externalId;
  }

  const isPending = (j: LocalJob) => j.state === "queued" || j.state === "processing";

  async function prune(): Promise<void> {
    const now = Date.now();
    for (const [id, job] of jobs) {
      if (!isPending(job) && now - job.startedAt > KEEP_MS) jobs.delete(id);
    }
    try {
      for (const name of await readdir(workRoot)) {
        if (!JOB_ID.test(name) || jobs.has(name)) continue;
        const info = await stat(path.join(workRoot, name)).catch(() => null);
        if (info && now - info.mtimeMs > KEEP_MS) await rm(path.join(workRoot, name), { recursive: true, force: true });
      }
    } catch {
      /* the folder may not exist yet */
    }
  }

  async function run(id: string, ffmpegPath: string): Promise<void> {
    const job = jobs.get(id);
    if (!job || job.state === "canceled" || !job.spec) return;
    const spec = job.spec;
    job.state = "processing";
    try {
      await renderMontage(spec, {
        ffmpegPath,
        workDir: jobDir(id),
        signal: job.controller.signal,
        onProgress: (fraction) => {
          job.progress = Math.round(fraction * 100);
        },
        // A render saturates the CPU: lower its priority so the website stays responsive.
        onProcess: (child) => {
          if (child?.pid) {
            try {
              os.setPriority(child.pid, 10);
            } catch {
              /* not permitted: keep the default priority */
            }
          }
        },
      });
      if ((job.state as JobState) !== "canceled") {
        job.state = "done";
        job.progress = 100;
      }
    } catch (error) {
      if (isMontageError(error) && error.canceled) job.state = "canceled";
      else {
        job.state = "failed";
        // Only our own messages are user-safe; anything else may contain server paths.
        job.message = isMontageError(error) ? error.message : "Le montage a échoué (erreur interne, voir les journaux du serveur).";
        console.error("[montage] render failed", error);
      }
    } finally {
      job.spec = null; // free the photos
    }
  }

  return {
    id: "montage",
    label: "Montage local (gratuit, sans IA)",
    envVars: ["FFMPEG_PATH"],
    models: [MODEL],
    isConfigured: () => findFfmpeg() !== null,

    async submit(input: SubmitInput): Promise<SubmitResult> {
      const spec = input.montage;
      if (!spec) throw new AppError("Le montage a besoin des photos et du texte du projet.");
      const ffmpegPath = findFfmpeg();
      if (!ffmpegPath) {
        throw new AppError("ffmpeg est introuvable : lancez « npm install » ou définissez FFMPEG_PATH.", "NOT_CONFIGURED");
      }
      if ([...jobs.values()].filter(isPending).length >= MAX_PENDING_JOBS) {
        throw new AppError("Trop de montages en cours sur ce serveur. Réessayez dans un instant.", "QUOTA");
      }

      await ensurePrivateRoot(workRoot);
      await prune();
      if (!pruneTimer) {
        pruneTimer = setInterval(() => void prune(), PRUNE_EVERY_MS);
        pruneTimer.unref();
      }

      const id = randomUUID();
      await mkdir(jobDir(id), { mode: 0o700 });
      jobs.set(id, { state: "queued", progress: 0, startedAt: Date.now(), controller: new AbortController(), spec });
      queue = queue.then(() => run(id, ffmpegPath)).catch(() => undefined);
      return { externalId: id };
    },

    async poll(job: JobRef): Promise<ProviderJobStatus> {
      const id = checkedId(job.externalId);
      const local = jobs.get(id);
      if (local) {
        switch (local.state) {
          case "queued":
            return { state: "queued" };
          case "processing":
            return { state: "processing", progress: local.progress };
          case "done":
            return { state: "succeeded", output: { videoUrl: `local:${id}` } };
          case "canceled":
            return { state: "canceled" };
          default:
            return { state: "failed", message: local.message ?? "Le montage a échoué." };
        }
      }
      // Not in memory: the server restarted since the job was submitted.
      // result.mp4 only ever exists complete (it is renamed into place after ffmpeg succeeds).
      if (existsSync(resultPath(id))) return { state: "succeeded", output: { videoUrl: `local:${id}` } };
      if (existsSync(jobDir(id))) {
        return { state: "failed", message: "Le montage a été interrompu par un redémarrage du serveur. Relancez-le." };
      }
      return { state: "failed", message: "Montage introuvable : ses fichiers temporaires ont été supprimés." };
    },

    async download(job: JobRef, _status: SucceededStatus): Promise<Response> {
      void _status;
      const id = checkedId(job.externalId);
      const info = await stat(resultPath(id)).catch(() => null);
      if (!info || info.size === 0) {
        throw new ProviderApiError("Montage", 0, "Le fichier du montage est introuvable.", false);
      }
      return new Response(Readable.toWeb(createReadStream(resultPath(id))) as ReadableStream, {
        headers: { "content-type": "video/mp4", "content-length": String(info.size) },
      });
    },

    async cancel(job: JobRef): Promise<void> {
      const id = checkedId(job.externalId);
      const local = jobs.get(id);
      if (!local || !isPending(local)) return;
      local.state = "canceled";
      local.spec = null; // do not hold the photos while waiting in the queue
      local.controller.abort();
    },

    async cleanup(job: JobRef): Promise<void> {
      const id = checkedId(job.externalId);
      const local = jobs.get(id);
      if (local && isPending(local)) return; // never remove the folder of a running job
      jobs.delete(id);
      await rm(jobDir(id), { recursive: true, force: true });
    },
  };
}
