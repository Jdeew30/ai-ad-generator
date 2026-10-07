import type { Generation } from "@/generated/prisma/client";
import { findModel, type ModelEntry } from "@/modules/video-providers";

/** What the UI needs to know about a model — never any credential. */
export interface ModelOptionView {
  id: string;
  label: string;
  description: string;
  providerLabel: string;
  configured: boolean;
  envVars: string[];
  durations: number[];
  aspectRatios: string[];
  imageToVideo: boolean;
}

export function toModelOptionView(entry: ModelEntry): ModelOptionView {
  return {
    id: entry.profile.id,
    label: entry.profile.label,
    description: entry.profile.description,
    providerLabel: entry.provider.label,
    configured: entry.configured,
    envVars: [...entry.provider.envVars],
    durations: [...entry.profile.capabilities.durations],
    aspectRatios: [...entry.profile.capabilities.aspectRatios],
    imageToVideo: entry.profile.capabilities.imageToVideo,
  };
}

/** Serializable view of a generation for client components / the status API. */
export interface GenerationDto {
  id: string;
  status: Generation["status"];
  progress: number | null;
  errorMessage: string | null;
  providerLabel: string;
  modelLabel: string;
  durationSeconds: number;
  aspectRatio: string;
  prompt: string;
  createdAt: string;
  submittedAt: string | null;
  completedAt: string | null;
  /** True only for a SUCCEEDED generation whose file is stored and verified. */
  hasVideo: boolean;
  outputBytes: number | null;
  canCancel: boolean;
}

export function toGenerationDto(g: Generation): GenerationDto {
  const entry = findModel(g.modelId);
  const canCancel = g.status === "PENDING" || (g.status === "PROCESSING" && Boolean(entry?.provider.cancel));
  return {
    id: g.id,
    status: g.status,
    progress: g.progress,
    errorMessage: g.errorMessage,
    providerLabel: entry?.provider.label ?? g.providerId,
    modelLabel: entry?.profile.label ?? g.modelId,
    durationSeconds: g.durationSeconds,
    aspectRatio: g.aspectRatio,
    prompt: g.prompt,
    createdAt: g.createdAt.toISOString(),
    submittedAt: g.submittedAt?.toISOString() ?? null,
    completedAt: g.completedAt?.toISOString() ?? null,
    hasVideo: g.status === "SUCCEEDED" && Boolean(g.outputKey),
    outputBytes: g.outputBytes,
    canCancel,
  };
}
