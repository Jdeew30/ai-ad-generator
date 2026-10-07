"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { runAction, type ActionResult } from "@/lib/action";
import { getDb } from "@/lib/db";
import { requireUser } from "@/lib/session";
import { generateConcept, updateConceptPrompt } from "./service";

const id = z.string().min(1).max(64);

export async function generateConceptAction(projectId: string): Promise<ActionResult<{ conceptId: string }>> {
  const user = await requireUser();
  const result = await runAction(async () => {
    const concept = await generateConcept(getDb(), user.id, id.parse(projectId));
    return { conceptId: concept.id };
  });
  if (result.ok) revalidatePath(`/projects/${projectId}`);
  return result;
}

export async function saveConceptPromptAction(
  projectId: string,
  conceptId: string,
  input: { videoPrompt: string; negativePrompt: string },
): Promise<ActionResult> {
  const user = await requireUser();
  const parsed = z
    .object({ videoPrompt: z.string().max(5000), negativePrompt: z.string().max(2000) })
    .safeParse(input);
  if (!parsed.success) return { ok: false, error: "Données invalides." };

  const result = await runAction(async () => {
    await updateConceptPrompt(getDb(), user.id, id.parse(conceptId), parsed.data);
    return undefined;
  });
  if (result.ok) revalidatePath(`/projects/${projectId}`);
  return result;
}
