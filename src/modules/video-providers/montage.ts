import { randomUUID } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { mkdir, readdir, rm, stat } from "node:fs/promises";
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
 * brief's text with ffmpeg on this machine. It is free, and always presented as such.
 *
 * Jobs run one at a time (a render saturates the CPU). State lives in memory for
 * the running process; the finished file lives on disk, so a finished job survives
 * a restart while an interrupted one is reported as interrupted (never as success).
 */

const MODEL: VideoModelProfile = {
  id: "montage:photo-slideshow",
  providerId: "montage",
  label: "Montage photo (gratuit)",
  description:
    "Anime vos photos produit (zoom lent, fondus) avec le nom, les avantages et l'appel à l'action de votre brief. Vidéo MP4 muette, réalisée sur votre ordinateur. Ce n'est pas de l'IA.",
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

type JobState = "queued" | "processing" | "done" | "failed" | "canceled";

interface LocalJob {
  state: JobState;
  progress: number;
  message?: string;
  startedAt: number;
  controller: AbortController;
}

export interface MontageProviderOptions {
  /** Where job folders are created. Defaults to the OS temp directory. */
  workRoot?: string;
  /** Test seam. */
  ffmpegPath?: () => string | null;
}

export function createMontageProvider(options: MontageProviderOptions = {}): VideoProvider {
  const workRoot = options.workRoot ?? path.join(os.tmpdir(), "adstudio-montage");
  const findFfmpeg = options.ffmpegPath ?? resolveFfmpeg;
  const jobs = new Map<string, LocalJob>();
  let queue: Promise<void> = Promise.resolve();

  const jobDir = (id: string) => path.join(workRoot, id);
  const resultPath = (id: string) => path.join(jobDir(id), "result.mp4");

  function checkedId(externalId: string): string {
    if (!JOB_ID.test(externalId)) throw new ProviderApiError("Montage", 0, "Identifiant de montage invalide.", false);
    return externalId;
  }

  async function prune(): Promise<void> {
    const now = Date.now();
    for (const [id, job] of jobs) {
      if (job.state !== "queued" && job.state !== "processing" && now - job.startedAt > KEEP_MS) jobs.delete(id);
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

  async function run(id: string, spec: MontageSpec, ffmpegPath: string): Promise<void> {
    const job = jobs.get(id);
    if (!job) return;
    if (job.state === "canceled") return;
    job.state = "processing";
    try {
      await renderMontage(spec, {
        ffmpegPath,
        workDir: jobDir(id),
        signal: job.controller.signal,
        onProgress: (fraction) => {
          job.progress = Math.round(fraction * 100);
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
        job.message = error instanceof Error ? error.message : "Le montage a échoué.";
        console.error("[montage] render failed", error);
      }
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

      await prune();
      const id = randomUUID();
      await mkdir(jobDir(id), { recursive: true });
      jobs.set(id, { state: "queued", progress: 0, startedAt: Date.now(), controller: new AbortController() });
      queue = queue.then(() => run(id, spec, ffmpegPath)).catch(() => undefined);
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
      if (!local || (local.state !== "queued" && local.state !== "processing")) return;
      local.state = "canceled";
      local.controller.abort();
    },
  };
}
