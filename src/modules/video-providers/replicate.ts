import { AppError } from "@/lib/errors";
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
 * Replicate predictions API.
 * Transport contract verified against the official `replicate` SDK source (v1.4):
 *   POST /models/{owner}/{name}/predictions   { input }          Authorization: Bearer
 *   GET  /predictions/{id}      status: starting|processing|succeeded|failed|canceled|aborted
 *   POST /predictions/{id}/cancel
 * Data URIs are an officially supported way to pass file inputs (SDK "data-uri" strategy).
 *
 * The per-model `buildInput` mappings below come from the public model
 * documentation and have NOT been exercised against the live API in this build
 * (no credentials available). A wrong mapping surfaces as a provider validation
 * error on the generation, never as a fake success.
 */

const PROVIDER = "Replicate";
const DEFAULT_BASE_URL = "https://api.replicate.com/v1";

interface ReplicateModelSpec {
  profile: VideoModelProfile;
  /** Native "owner/name". */
  slug: string;
  buildInput(input: SubmitInput): Record<string, unknown>;
}

function toDataUri(image: { bytes: Uint8Array; mimeType: string }): string {
  return `data:${image.mimeType};base64,${Buffer.from(image.bytes).toString("base64")}`;
}

const SPECS: ReplicateModelSpec[] = [
  {
    slug: "bytedance/seedance-1-lite",
    profile: {
      id: "replicate:bytedance/seedance-1-lite",
      providerId: "replicate",
      label: "Seedance 1 Lite",
      description: "ByteDance via Replicate — texte ou image vers vidéo, rapide et économique.",
      capabilities: { durations: [5, 10], aspectRatios: ["16:9", "9:16", "1:1"], imageToVideo: true, supportsNegativePrompt: false },
    },
    buildInput: (input) => ({
      prompt: input.prompt,
      duration: input.durationSeconds,
      aspect_ratio: input.aspectRatio,
      ...(input.referenceImage ? { image: toDataUri(input.referenceImage) } : {}),
    }),
  },
];

interface ReplicatePrediction {
  id: string;
  status: "starting" | "processing" | "succeeded" | "failed" | "canceled" | "aborted";
  output?: unknown;
  error?: unknown;
}

/** Video models return a URL string, an array of URLs, or an object holding one. */
export function extractReplicateVideoUrl(output: unknown): string | undefined {
  if (typeof output === "string") return output;
  if (Array.isArray(output)) return output.find((o): o is string => typeof o === "string");
  if (output && typeof output === "object") {
    const o = output as Record<string, unknown>;
    for (const key of ["video", "url", "output"]) {
      if (typeof o[key] === "string") return o[key] as string;
    }
  }
  return undefined;
}

export interface ReplicateProviderOptions extends AdapterDeps {
  apiToken?: string;
  baseUrl?: string;
}

export function createReplicateProvider(options: ReplicateProviderOptions): VideoProvider {
  const baseUrl = (options.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, "");
  const token = options.apiToken;

  function headers(extra: Record<string, string> = {}): Record<string, string> {
    if (!token) throw new AppError("Replicate n'est pas configuré (REPLICATE_API_TOKEN manquant).", "NOT_CONFIGURED");
    return { Authorization: `Bearer ${token}`, ...extra };
  }

  function specFor(modelId: string): ReplicateModelSpec {
    const spec = SPECS.find((s) => s.profile.id === modelId);
    if (!spec) throw new AppError(`Modèle Replicate inconnu : ${modelId}`);
    return spec;
  }

  return {
    id: "replicate",
    label: "Replicate",
    envVars: ["REPLICATE_API_TOKEN"],
    models: SPECS.map((s) => s.profile),
    isConfigured: () => Boolean(token),

    async submit(input: SubmitInput): Promise<SubmitResult> {
      const spec = specFor(input.model.id);
      const prediction = await requestJson<ReplicatePrediction>(
        PROVIDER,
        `${baseUrl}/models/${spec.slug}/predictions`,
        {
          method: "POST",
          headers: headers({ "Content-Type": "application/json" }),
          body: JSON.stringify({ input: spec.buildInput(input) }),
        },
        { fetch: options.fetch, signal: input.signal, retries: 0, timeoutMs: 60_000 },
      );
      if (!prediction?.id) throw new ProviderApiError(PROVIDER, 502, "Réponse inattendue : identifiant de prédiction manquant.", false);
      return { externalId: prediction.id };
    },

    async poll(job: JobRef): Promise<ProviderJobStatus> {
      const p = await requestJson<ReplicatePrediction>(
        PROVIDER,
        `${baseUrl}/predictions/${encodeURIComponent(job.externalId)}`,
        { method: "GET", headers: headers() },
        { fetch: options.fetch, retries: 2 },
      );
      switch (p.status) {
        case "starting":
          return { state: "queued" };
        case "processing":
          return { state: "processing" };
        case "succeeded": {
          const videoUrl = extractReplicateVideoUrl(p.output);
          if (!videoUrl) {
            return { state: "failed", message: "Replicate a terminé sans renvoyer de fichier vidéo exploitable." };
          }
          return { state: "succeeded", output: { videoUrl } };
        }
        case "failed":
          return { state: "failed", message: typeof p.error === "string" && p.error ? p.error : "Échec de la génération chez Replicate." };
        case "canceled":
        case "aborted":
          return { state: "canceled" };
        default:
          throw new ProviderApiError(PROVIDER, 502, `Statut inconnu renvoyé par Replicate : ${String(p.status)}`, true);
      }
    },

    async download(_job: JobRef, status: SucceededStatus): Promise<Response> {
      const url = status.output?.videoUrl;
      if (!url) throw new ProviderApiError(PROVIDER, 0, "URL de la vidéo manquante.", false);
      // Delivery URLs are public: no credentials are sent.
      return fetchFollowingRedirects(url, {}, { fetch: options.fetch, policy: options.urlPolicy, provider: PROVIDER });
    },

    async cancel(job: JobRef): Promise<void> {
      await requestJson<unknown>(
        PROVIDER,
        `${baseUrl}/predictions/${encodeURIComponent(job.externalId)}/cancel`,
        { method: "POST", headers: headers() },
        { fetch: options.fetch },
      );
    },
  };
}
