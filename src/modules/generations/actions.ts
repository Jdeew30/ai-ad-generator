"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { runAction, type ActionResult } from "@/lib/action";
import { getDb } from "@/lib/db";
import { requireUser } from "@/lib/session";
import { cancelGeneration, deleteGeneration, startGeneration } from "./service";

const id = z.string().min(1).max(64);

const startSchema = z.object({
  projectId: id,
  modelId: z.string().min(1).max(200),
  durationSeconds: z.number().int().positive().max(60),
  referenceAssetId: id.nullable().optional(),
});

export interface StartResult {
  id: string;
  status: string;
  /** Set when the provider rejected the job: the generation is recorded as FAILED. */
  errorMessage: string | null;
}

export async function startGenerationAction(input: unknown): Promise<ActionResult<StartResult>> {
  const user = await requireUser();
  const parsed = startSchema.safeParse(input);
  if (!parsed.success) return { ok: false, error: "Paramètres de génération invalides." };

  const result = await runAction(async () => {
    const g = await startGeneration(getDb(), user.id, parsed.data);
    return { id: g.id, status: g.status, errorMessage: g.errorMessage };
  });
  // Even a failed submission is recorded and must show up in the history.
  revalidatePath(`/projects/${parsed.data.projectId}`);
  return result;
}

export async function cancelGenerationAction(projectId: string, generationId: string): Promise<ActionResult> {
  const user = await requireUser();
  const result = await runAction(async () => {
    await cancelGeneration(getDb(), user.id, id.parse(generationId));
    return undefined;
  });
  revalidatePath(`/projects/${projectId}`);
  return result;
}

export async function deleteGenerationAction(projectId: string, generationId: string): Promise<ActionResult> {
  const user = await requireUser();
  const result = await runAction(async () => {
    await deleteGeneration(getDb(), user.id, id.parse(generationId));
    return undefined;
  });
  revalidatePath(`/projects/${projectId}`);
  return result;
}
