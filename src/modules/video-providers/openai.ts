import { AppError } from "@/lib/errors";
import type { AspectRatio } from "@/modules/projects/presets";
import { ProviderApiError, fetchFollowingRedirects, requestJson } from "./http";
import type {
  AdapterDeps,
  JobRef,
  ProviderJobStatus,
  SubmitInput,
  SubmitResult,
  SucceededStatus,
  VideoModelProfile,
  VideoProvider,
} from "./types";

/**
 * OpenAI Videos API (Sora).
 * Contract verified against the official `openai` SDK (v7) type definitions:
 *   POST /videos           multipart: prompt, model, seconds ("4"|"8"|"12"), size
 *   GET  /videos/{id}      -> { id, status: queued|in_progress|completed|failed, progress, error }
 *   GET  /videos/{id}/content  -> binary video (requires the API key)
 * No cancellation endpoint exists, so `cancel` is intentionally not implemented.
 * Image reference is not enabled: the API requires the image to match the output
 * size exactly, which the MVP does not handle.
 */

const PROVIDER = "OpenAI";
const DEFAULT_BASE_URL = "https://api.openai.com/v1";

const SIZE_BY_ASPECT: Partial<Record<AspectRatio, string>> = {
  "9:16": "720x1280",
  "16:9": "1280x720",
};

const MODELS: VideoModelProfile[] = [
  {
    id: "openai:sora-2",
    providerId: "openai",
    label: "Sora 2",
    description: "OpenAI — texte vers vidéo, vertical ou horizontal.",
    capabilities: { durations: [4, 8, 12], aspectRatios: ["9:16", "16:9"], imageToVideo: false, supportsNegativePrompt: false },
  },
  {
    id: "openai:sora-2-pro",
    providerId: "openai",
    label: "Sora 2 Pro",
    description: "OpenAI — variante haute qualité, plus lente et plus coûteuse.",
    capabilities: { durations: [4, 8, 12], aspectRatios: ["9:16", "16:9"], imageToVideo: false, supportsNegativePrompt: false },
  },
];

interface OpenAiVideo {
  id: string;
  status: "queued" | "in_progress" | "completed" | "failed";
  progress?: number;
  error?: { code?: string; message?: string } | null;
}

export interface OpenAiProviderOptions extends AdapterDeps {
  apiKey?: string;
  baseUrl?: string;
}

export function createOpenAiProvider(options: OpenAiProviderOptions): VideoProvider {
  const baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, "");
  const apiKey = options.apiKey;

  function auth(): Record<string, string> {
    if (!apiKey) throw new AppError("OpenAI n'est pas configuré (OPENAI_API_KEY manquant).", "NOT_CONFIGURED");
    return { Authorization: `Bearer ${apiKey}` };
  }

  function nativeModel(modelId: string): string {
    return modelId.replace(/^openai:/, "");
  }

  return {
    id: "openai",
    label: "OpenAI Sora",
    envVars: ["OPENAI_API_KEY"],
    models: MODELS,
    isConfigured: () => Boolean(apiKey),

    async submit(input: SubmitInput): Promise<SubmitResult> {
      const size = SIZE_BY_ASPECT[input.aspectRatio];
      if (!size) throw new AppError(`Sora ne supporte pas le format ${input.aspectRatio}.`);

      const form = new FormData();
      form.set("model", nativeModel(input.model.id));
      form.set("prompt", input.prompt);
      form.set("seconds", String(input.durationSeconds));
      form.set("size", size);

      const video = await requestJson<OpenAiVideo>(
        PROVIDER,
        `${baseUrl}/videos`,
        { method: "POST", headers: auth(), body: form },
        { fetch: options.fetch, signal: input.signal, retries: 0 },
      );
      if (!video?.id) throw new ProviderApiError(PROVIDER, 502, "Réponse inattendue : identifiant de job manquant.", false);
      return { externalId: video.id };
    },

    async poll(job: JobRef): Promise<ProviderJobStatus> {
      const video = await requestJson<OpenAiVideo>(
        PROVIDER,
        `${baseUrl}/videos/${encodeURIComponent(job.externalId)}`,
        { method: "GET", headers: auth() },
        { fetch: options.fetch, retries: 2 },
      );
      switch (video.status) {
        case "queued":
          return { state: "queued" };
        case "in_progress":
          return { state: "processing", progress: typeof video.progress === "number" ? Math.round(video.progress) : undefined };
        case "completed":
          return { state: "succeeded" };
        case "failed":
          return { state: "failed", message: video.error?.message || video.error?.code || "Échec de la génération chez OpenAI." };
        default:
          // Unknown status: do not guess. Surface it as a retryable anomaly.
          throw new ProviderApiError(PROVIDER, 502, `Statut inconnu renvoyé par OpenAI : ${String(video.status)}`, true);
      }
    },

    async download(job: JobRef, _status: SucceededStatus): Promise<Response> {
      void _status;
      return fetchFollowingRedirects(
        `${baseUrl}/videos/${encodeURIComponent(job.externalId)}/content`,
        { headers: auth() },
        { fetch: options.fetch, policy: options.urlPolicy, provider: PROVIDER },
      );
    },
  };
}
