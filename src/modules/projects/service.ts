import type { Db } from "@/lib/db";
import { AppError } from "@/lib/errors";
import { getStorage } from "@/modules/storage";
import type { Brief } from "./schema";

/**
 * Every function takes the acting `userId` and scopes its queries by it, so a
 * guessed project id can never leak another user's data (no IDOR).
 */

export async function createProject(db: Db, userId: string, brief: Brief) {
  return db.project.create({
    data: {
      userId,
      productName: brief.productName,
      description: brief.description,
      benefits: brief.benefits,
      targetAudience: brief.targetAudience,
      tone: brief.tone,
      visualStyle: brief.visualStyle,
      aspectRatio: brief.aspectRatio,
      durationSeconds: brief.durationSeconds,
      language: brief.language,
      callToAction: brief.callToAction ?? null,
      creativeNotes: brief.creativeNotes ?? null,
    },
  });
}

export async function updateProject(db: Db, userId: string, projectId: string, brief: Brief) {
  const result = await db.project.updateMany({
    where: { id: projectId, userId },
    data: {
      productName: brief.productName,
      description: brief.description,
      benefits: brief.benefits,
      targetAudience: brief.targetAudience,
      tone: brief.tone,
      visualStyle: brief.visualStyle,
      aspectRatio: brief.aspectRatio,
      durationSeconds: brief.durationSeconds,
      language: brief.language,
      callToAction: brief.callToAction ?? null,
      creativeNotes: brief.creativeNotes ?? null,
    },
  });
  if (result.count === 0) throw new AppError("Projet introuvable.", "NOT_FOUND");
}

export async function getProjectForUser(db: Db, userId: string, projectId: string) {
  const project = await db.project.findFirst({
    where: { id: projectId, userId },
    include: {
      assets: { orderBy: { createdAt: "asc" } },
      concepts: { orderBy: { version: "desc" }, take: 1 },
    },
  });
  if (!project) throw new AppError("Projet introuvable.", "NOT_FOUND");
  return project;
}

export async function listProjects(db: Db, userId: string) {
  return db.project.findMany({
    where: { userId },
    orderBy: { updatedAt: "desc" },
    include: {
      _count: { select: { assets: true, generations: true } },
      generations: {
        where: { status: "SUCCEEDED" },
        orderBy: { completedAt: "desc" },
        take: 1,
        select: { id: true },
      },
    },
  });
}

/** Deletes the project (rows cascade) and every stored file that belonged to it. */
export async function deleteProject(db: Db, userId: string, projectId: string) {
  const project = await db.project.findFirst({
    where: { id: projectId, userId },
    select: {
      id: true,
      assets: { select: { storageKey: true } },
      generations: { select: { outputKey: true } },
    },
  });
  if (!project) throw new AppError("Projet introuvable.", "NOT_FOUND");

  await db.project.delete({ where: { id: project.id } });

  const storage = getStorage();
  const keys = [
    ...project.assets.map((a) => a.storageKey),
    ...project.generations.map((g) => g.outputKey).filter((k): k is string => Boolean(k)),
  ];
  // Rows are gone; best-effort file cleanup must not fail the request.
  await Promise.allSettled(keys.map((key) => storage.delete(key)));
}
