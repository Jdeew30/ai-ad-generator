"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { runAction, type ActionResult } from "@/lib/action";
import { getDb } from "@/lib/db";
import { requireUser } from "@/lib/session";
import { abortActiveGenerations } from "@/modules/generations/service";
import { briefSchema } from "./schema";
import { createProject, deleteProject, updateProject } from "./service";

function fieldErrorsOf(error: import("zod").ZodError): Record<string, string> {
  const out: Record<string, string> = {};
  for (const issue of error.issues) {
    const key = String(issue.path[0] ?? "form");
    out[key] ??= issue.message;
  }
  return out;
}

export async function createProjectAction(input: unknown): Promise<ActionResult<{ id: string }>> {
  const user = await requireUser();
  const parsed = briefSchema.safeParse(input);
  if (!parsed.success) {
    return { ok: false, error: "Le brief contient des erreurs.", fieldErrors: fieldErrorsOf(parsed.error) };
  }
  const result = await runAction(async () => {
    const project = await createProject(getDb(), user.id, parsed.data);
    return { id: project.id };
  });
  if (result.ok) revalidatePath("/dashboard");
  return result;
}

export async function updateProjectAction(projectId: string, input: unknown): Promise<ActionResult<{ id: string }>> {
  const user = await requireUser();
  const parsed = briefSchema.safeParse(input);
  if (!parsed.success) {
    return { ok: false, error: "Le brief contient des erreurs.", fieldErrors: fieldErrorsOf(parsed.error) };
  }
  const result = await runAction(async () => {
    await updateProject(getDb(), user.id, projectId, parsed.data);
    return { id: projectId };
  });
  if (result.ok) {
    revalidatePath(`/projects/${projectId}`);
    revalidatePath("/dashboard");
  }
  return result;
}

export async function deleteProjectAction(projectId: string): Promise<ActionResult> {
  const user = await requireUser();
  const result = await runAction(async () => {
    const db = getDb();
    await abortActiveGenerations(db, user.id, projectId);
    await deleteProject(db, user.id, projectId);
    return undefined;
  });
  if (!result.ok) return result;
  revalidatePath("/dashboard");
  redirect("/dashboard");
}
