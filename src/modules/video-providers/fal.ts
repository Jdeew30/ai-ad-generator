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
 * fal.ai queue API.
 * Transport contract verified against the official `@fal-ai/client` source (v1.10):
 *   POST {queue}/{endpoint}                                 body = model input, Authorization: Key <key>
 *   GET  {queue}/{owner}/{alias}/requests/{id}/status       IN_QUEUE | IN_PROGRESS | COMPLETED
 *   GET  {queue}/{owner}/{alias}/requests/{id}              the result (or an error status)
 *   PUT  {queue}/{owner}/{alias}/requests/{id}/cancel
 * Note: status/result/cancel use only `{owner}/{alias}` (the first two segments of
 * the endpoint), not the full submit path. `COMPLETED` does NOT imply success: the
 * result request is what tells whether a video was produced.
 * We rebuild those URLs ourselves (as the SDK does) instead of following URLs from
 * the response, so our credential is only ever sent to the configured queue host.
 *
 * Per-model `buildInput` mappings come from public model documentation and have
 * NOT been exercised against the live API in this build (no credentials).
 */

const PROVIDER = "fal.ai";
const DEFAULT_QUEUE_URL = "https://queue.fal.run";

interface FalModelSpec {
  profile: VideoModelProfile;
  textEndpoint: string;
  imageEndpoint?: string;
  buildInput(input: SubmitInput, mode: "text" | "image"): Record<string, unknown>;
}

function toDataUri(image: { bytes: Uint8Array; mimeType: string }): string {
  return `data:${image.mimeType};base64,${Buffer.from(image.bytes).toString("base64")}`;
}

const SPECS: FalModelSpec[] = [
  {
    profile: {
      id: "fal:fal-ai/kling-video/v2.1/master",
      providerId: "fal",
      label: "Kling 2.1 Master",
      description: "Kuaishou via fal.ai — texte ou image vers vidéo, mouvement soigné.",
      capabilities: { durations: [5, 10], aspectRatios: ["16:9", "9:16", "1:1"], imageToVideo: true, supportsNegativePrompt: true },
    },
    textEndpoint: "fal-ai/kling-video/v2.1/master/text-to-video",
    imageEndpoint: "fal-ai/kling-video/v2.1/master/image-to-video",
    buildInput: (input, mode) => ({
      prompt: input.prompt,
      duration: String(input.durationSeconds),
      ...(mode === "text" ? { aspect_ratio: input.aspectRatio } : { image_url: toDataUri(input.referenceImage!) }),
      ...(input.negativePrompt ? { negative_prompt: input.negativePrompt } : {}),
    }),
  },
];

interface FalSubmitResponse {
  request_id?: string;
}
interface FalStatusResponse {
  status?: "IN_QUEUE" | "IN_PROGRESS" | "COMPLETED";
}
interface FalResult {
  video?: { url?: string };
}

/** `fal-ai/kling-video/v2.1/master/text-to-video` -> `fal-ai/kling-video` (SDK parseEndpointId). */
export function falAppPath(endpoint: string): string {
  const parts = endpoint.split("/").filter(Boolean);
  if ((parts[0] === "workflows" || parts[0] === "comfy") && parts.length >= 3) {
    return parts.slice(0, 3).join("/");
  }
  if (parts.length < 2) throw new AppError(`Endpoint fal invalide : ${endpoint}`);
  return parts.slice(0, 2).join("/");
}

export interface FalProviderOptions extends AdapterDeps {
  apiKey?: string;
  queueUrl?: string;
}

export function createFalProvider(options: FalProviderOptions): VideoProvider {
  const queueUrl = (options.queueUrl ?? DEFAULT_QUEUE_URL).replace(/\/$/, "");
  const apiKey = options.apiKey;

  function headers(extra: Record<string, string> = {}): Record<string, string> {
    if (!apiKey) throw new AppError("fal.ai n'est pas configuré (FAL_KEY manquant).", "NOT_CONFIGURED");
    return { Authorization: `Key ${apiKey}`, Accept: "application/json", ...extra };
  }

  function specFor(modelId: string): FalModelSpec {
    const spec = SPECS.find((s) => s.profile.id === modelId);
    if (!spec) throw new AppError(`Modèle fal inconnu : ${modelId}`);
    return spec;
  }

  function endpointOf(job: JobRef): string {
    const endpoint = job.providerData?.endpoint;
    if (typeof endpoint !== "string") throw new ProviderApiError(PROVIDER, 0, "Données de job fal manquantes.", false);
    return endpoint;
  }

  function requestUrl(job: JobRef, suffix = ""): string {
    return `${queueUrl}/${falAppPath(endpointOf(job))}/requests/${encodeURIComponent(job.externalId)}${suffix}`;
  }

  return {
    id: "fal",
    label: "fal.ai",
    envVars: ["FAL_KEY"],
    models: SPECS.map((s) => s.profile),
    isConfigured: () => Boolean(apiKey),

    async submit(input: SubmitInput): Promise<SubmitResult> {
      const spec = specFor(input.model.id);
      const mode = input.referenceImage ? "image" : "text";
      const endpoint = mode === "image" ? spec.imageEndpoint : spec.textEndpoint;
      if (!endpoint) throw new AppError(`${spec.profile.label} n'accepte pas d'image de référence.`);

      const res = await requestJson<FalSubmitResponse>(
        PROVIDER,
        `${queueUrl}/${endpoint}`,
        {
          method: "POST",
          headers: headers({ "Content-Type": "application/json" }),
          body: JSON.stringify(spec.buildInput(input, mode)),
        },
        { fetch: options.fetch, signal: input.signal, retries: 0, timeoutMs: 60_000 },
      );
      if (!res?.request_id) throw new ProviderApiError(PROVIDER, 502, "Réponse inattendue : request_id manquant.", false);
      return { externalId: res.request_id, providerData: { endpoint } };
    },

    async poll(job: JobRef): Promise<ProviderJobStatus> {
      const status = await requestJson<FalStatusResponse>(
        PROVIDER,
        requestUrl(job, "/status"),
        { method: "GET", headers: headers() },
        { fetch: options.fetch, retries: 2 },
      );
      if (status.status === "IN_QUEUE") return { state: "queued" };
      if (status.status === "IN_PROGRESS") return { state: "processing" };
      if (status.status !== "COMPLETED") {
        throw new ProviderApiError(PROVIDER, 502, `Statut inconnu renvoyé par fal : ${String(status.status)}`, true);
      }

      // COMPLETED: the result request decides between success and failure.
      let result: FalResult;
      try {
        result = await requestJson<FalResult>(
          PROVIDER,
          requestUrl(job),
          { method: "GET", headers: headers() },
          { fetch: options.fetch, retries: 2 },
        );
      } catch (error) {
        if (error instanceof ProviderApiError && !error.retryable) {
          return { state: "failed", message: error.message };
        }
        throw error;
      }
      const videoUrl = result?.video?.url;
      if (!videoUrl) return { state: "failed", message: "fal.ai a terminé sans renvoyer de fichier vidéo exploitable." };
      return { state: "succeeded", output: { videoUrl } };
    },

    async download(_job: JobRef, status: SucceededStatus): Promise<Response> {
      const url = status.output?.videoUrl;
      if (!url) throw new ProviderApiError(PROVIDER, 0, "URL de la vidéo manquante.", false);
      return fetchFollowingRedirects(url, {}, { fetch: options.fetch, policy: options.urlPolicy, provider: PROVIDER });
    },

    async cancel(job: JobRef): Promise<void> {
      await requestJson<unknown>(
        PROVIDER,
        requestUrl(job, "/cancel"),
        { method: "PUT", headers: headers() },
        { fetch: options.fetch },
      );
    },
  };
}
