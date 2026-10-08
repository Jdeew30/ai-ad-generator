import type { MontageSpec } from "@/modules/montage/types";
import type { AspectRatio } from "@/modules/projects/presets";

export type ProviderId = "openai" | "replicate" | "fal" | "montage";

/**
 * Declarative capabilities of one model. The UI and the server both validate a
 * request against these *before* anything is sent: we never submit a
 * combination (duration, format, image) the model is not declared to support.
 */
export interface VideoModelProfile {
  /** Globally unique, stable: `${providerId}:${slug}`. Stored in Generation.modelId. */
  id: string;
  providerId: ProviderId;
  label: string;
  description: string;
  capabilities: {
    durations: readonly number[];
    aspectRatios: readonly AspectRatio[];
    /** Accepts a reference image (image-to-video). */
    imageToVideo: boolean;
    /** If false, a negative prompt is folded into the prompt as an "Avoid:" clause. */
    supportsNegativePrompt: boolean;
    /**
     * Local photo montage (NOT generative AI): ignores the prompt, needs at least one
     * product photo, and runs on this machine at no cost.
     */
    montage?: boolean;
  };
}

export interface ReferenceImage {
  bytes: Uint8Array;
  mimeType: string;
}

export interface SubmitInput {
  model: VideoModelProfile;
  prompt: string;
  negativePrompt?: string;
  aspectRatio: AspectRatio;
  durationSeconds: number;
  referenceImage?: ReferenceImage;
  /** Only for models with `capabilities.montage`. */
  montage?: MontageSpec;
  signal?: AbortSignal;
}

export interface SubmitResult {
  /** Provider-side job identifier. */
  externalId: string;
  /** Opaque data the adapter needs again to poll/cancel/download. Persisted as JSON. */
  providerData?: Record<string, unknown>;
}

export interface JobRef {
  externalId: string;
  providerData: Record<string, unknown> | null;
  modelId: string;
}

export type ProviderJobStatus =
  | { state: "queued" }
  | { state: "processing"; progress?: number }
  /** `output` carries whatever `download()` needs (e.g. the file URL). */
  | { state: "succeeded"; output?: { videoUrl?: string } }
  | { state: "failed"; message: string }
  | { state: "canceled" };

export type SucceededStatus = Extract<ProviderJobStatus, { state: "succeeded" }>;

export interface VideoProvider {
  readonly id: ProviderId;
  readonly label: string;
  /** Environment variables that configure this provider (shown in the UI). */
  readonly envVars: readonly string[];
  readonly models: readonly VideoModelProfile[];
  /** True only when a real API credential is present. */
  isConfigured(): boolean;
  /**
   * Creates a job. MUST NOT be retried automatically: a retry after an
   * ambiguous failure could create (and bill) a second job.
   */
  submit(input: SubmitInput): Promise<SubmitResult>;
  /**
   * Reads the job state. MUST report `succeeded` only if a video really exists.
   * Transient failures (network, 5xx) are thrown as retryable ProviderApiError,
   * never reported as `failed`.
   */
  poll(job: JobRef): Promise<ProviderJobStatus>;
  /** Opens the finished video as a binary stream. Auth is handled by the adapter. */
  download(job: JobRef, status: SucceededStatus): Promise<Response>;
  /** Optional: absent when the provider has no cancellation endpoint. */
  cancel?(job: JobRef): Promise<void>;
}

export type UrlPolicy = "public-https" | "any";

export interface AdapterDeps {
  fetch?: typeof fetch;
  /** "any" exists solely so tests can target a local HTTP server; never set in production wiring. */
  urlPolicy?: UrlPolicy;
}

export function checkCompatibility(
  profile: VideoModelProfile,
  req: { aspectRatio: AspectRatio; durationSeconds: number; withImage: boolean; productImages?: number },
): string[] {
  const problems: string[] = [];
  const c = profile.capabilities;
  if (!c.aspectRatios.includes(req.aspectRatio)) {
    problems.push(`${profile.label} ne supporte pas le format ${req.aspectRatio} (formats : ${c.aspectRatios.join(", ")}).`);
  }
  if (!c.durations.includes(req.durationSeconds)) {
    problems.push(`${profile.label} ne supporte pas ${req.durationSeconds} s (durées : ${c.durations.join(", ")} s).`);
  }
  if (req.withImage && !c.imageToVideo) {
    problems.push(`${profile.label} n'accepte pas d'image de référence.`);
  }
  if (c.montage && (req.productImages ?? 0) < 1) {
    problems.push("Le montage a besoin d'au moins une photo produit : ajoutez-en une dans le projet.");
  }
  return problems;
}

/** Folds the negative prompt into the prompt for models that have no dedicated field. */
export function composePrompt(profile: VideoModelProfile, prompt: string, negativePrompt?: string | null): {
  prompt: string;
  negativePrompt?: string;
} {
  const negative = negativePrompt?.trim();
  if (!negative) return { prompt };
  if (profile.capabilities.supportsNegativePrompt) return { prompt, negativePrompt: negative };
  return { prompt: `${prompt}\n\nAvoid: ${negative}` };
}

export const MAX_PROMPT_CHARS = 2000;
