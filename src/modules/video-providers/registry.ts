import path from "node:path";
import { getEnv, type Env } from "@/lib/env";
import { createFalProvider } from "./fal";
import { createMontageProvider } from "./montage";
import { createOpenAiProvider } from "./openai";
import { createReplicateProvider } from "./replicate";
import type { AdapterDeps, ProviderId, VideoModelProfile, VideoProvider } from "./types";

/**
 * Builds every known provider from the environment. A provider whose key is
 * absent is still listed (so the UI can say "not configured" and name the
 * variable) but `isConfigured()` is false and it can never run a job.
 *
 * Adding a provider = one adapter file implementing `VideoProvider` + one line here.
 */
export function buildProviders(env: Env, deps: AdapterDeps = {}): VideoProvider[] {
  return [
    createOpenAiProvider({ apiKey: env.OPENAI_API_KEY, baseUrl: env.OPENAI_BASE_URL, ...deps }),
    createReplicateProvider({ apiToken: env.REPLICATE_API_TOKEN, baseUrl: env.REPLICATE_BASE_URL, ...deps }),
    createFalProvider({ apiKey: env.FAL_KEY, queueUrl: env.FAL_QUEUE_BASE_URL, ...deps }),
    // Local, free, NOT generative AI. Always listed last and always labelled as such.
    // Job folders live under the app's private storage folder, never in the shared OS temp dir.
    createMontageProvider({ workRoot: path.resolve(env.STORAGE_DIR, ".montage-tmp") }),
  ];
}

const globalForProviders = globalThis as unknown as { __providers?: VideoProvider[] };

export function getProviders(): VideoProvider[] {
  if (!globalForProviders.__providers) {
    globalForProviders.__providers = buildProviders(getEnv());
  }
  return globalForProviders.__providers;
}

/** Test seam: inject providers (e.g. real adapters pointed at a local test server). */
export function setProvidersForTests(providers: VideoProvider[] | undefined): void {
  globalForProviders.__providers = providers;
}

export function getProvider(id: ProviderId): VideoProvider | undefined {
  return getProviders().find((p) => p.id === id);
}

export interface ModelEntry {
  profile: VideoModelProfile;
  provider: VideoProvider;
  configured: boolean;
}

export function listModels(): ModelEntry[] {
  return getProviders().flatMap((provider) =>
    provider.models.map((profile) => ({ profile, provider, configured: provider.isConfigured() })),
  );
}

export function findModel(modelId: string): ModelEntry | undefined {
  return listModels().find((m) => m.profile.id === modelId);
}

/** True when at least one real generative-AI provider has credentials (the local montage does not count). */
export function hasConfiguredAiProvider(): boolean {
  return getProviders().some((p) => p.id !== "montage" && p.isConfigured());
}
