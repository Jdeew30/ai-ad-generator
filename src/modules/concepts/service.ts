import type { Concept } from "@/generated/prisma/client";
import type { Db } from "@/lib/db";
import { AppError } from "@/lib/errors";
import { prepareReferenceImage } from "@/modules/assets/reference-image";
import { getStorage } from "@/modules/storage";
import { MAX_PROMPT_CHARS } from "@/modules/video-providers/types";
import { getConceptGenerator } from "./index";
import { conceptSchema, type ConceptPayload } from "./schema";
import type { ConceptImage } from "./types";

/** LLM calls cost money: cap how many concepts one user can create per hour. */
const MAX_CONCEPTS_PER_HOUR = 20;
const VISION_MAX_EDGE = 1024;

async function readAll(stream: AsyncIterable<unknown>): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const c of stream) chunks.push(c as Buffer);
  return Buffer.concat(chunks);
}

export async function generateConcept(db: Db, userId: string, projectId: string): Promise<Concept> {
  const project = await db.project.findFirst({
    where: { id: projectId, userId },
    include: { assets: { orderBy: { createdAt: "asc" } } },
  });
  if (!project) throw new AppError("Projet introuvable.", "NOT_FOUND");

  const recent = await db.concept.count({
    where: { project: { userId }, createdAt: { gte: new Date(Date.now() - 60 * 60_000) } },
  });
  if (recent >= MAX_CONCEPTS_PER_HOUR) {
    throw new AppError(`Limite atteinte : ${MAX_CONCEPTS_PER_HOUR} concepts par heure. Réessayez plus tard.`, "QUOTA");
  }

  // Product photos first (they matter most for describing the product), then the logo.
  const ordered = [...project.assets].sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "PRODUCT_IMAGE" ? -1 : 1));
  const images: ConceptImage[] = [];
  for (const asset of ordered.slice(0, 3)) {
    try {
      const raw = await readAll(getStorage().createReadStream(asset.storageKey));
      const prepared = await prepareReferenceImage(raw, VISION_MAX_EDGE);
      images.push({ kind: asset.kind, mimeType: "image/jpeg", bytes: prepared.bytes });
    } catch (error) {
      // One unreadable image must not block the concept; the brief alone is enough.
      console.warn("[concept] skipping unreadable asset", asset.id, error);
    }
  }

  const generator = getConceptGenerator();
  const draft = await generator.generate({
    brief: {
      productName: project.productName,
      description: project.description,
      benefits: project.benefits,
      targetAudience: project.targetAudience,
      tone: project.tone,
      visualStyle: project.visualStyle,
      aspectRatio: project.aspectRatio,
      durationSeconds: project.durationSeconds,
      language: project.language,
      callToAction: project.callToAction,
      creativeNotes: project.creativeNotes,
    },
    images,
  });

  // Defence in depth: never persist a payload that did not pass strict validation.
  const payload = conceptSchema.parse(draft.payload);

  for (let attempt = 0; attempt < 3; attempt++) {
    const last = await db.concept.findFirst({
      where: { projectId },
      orderBy: { version: "desc" },
      select: { version: true },
    });
    try {
      return await db.concept.create({
        data: {
          projectId,
          version: (last?.version ?? 0) + 1,
          source: draft.source,
          model: draft.model,
          payload,
          videoPrompt: payload.videoPrompt,
          negativePrompt: payload.negativePrompt || null,
        },
      });
    } catch (error) {
      // Unique (projectId, version) collision from a concurrent request: retry.
      if ((error as { code?: string }).code !== "P2002" || attempt === 2) throw error;
    }
  }
  throw new AppError("Impossible d'enregistrer le concept. Réessayez.");
}

export async function getLatestConcept(db: Db, userId: string, projectId: string) {
  const concept = await db.concept.findFirst({
    where: { projectId, project: { userId } },
    orderBy: { version: "desc" },
  });
  if (!concept) return null;
  const parsed = conceptSchema.safeParse(concept.payload);
  return { concept, payload: parsed.success ? parsed.data : null };
}

/** The prompt actually sent to the video model is user-editable; this saves the edit. */
export async function updateConceptPrompt(
  db: Db,
  userId: string,
  conceptId: string,
  input: { videoPrompt: string; negativePrompt: string },
): Promise<Concept> {
  const videoPrompt = input.videoPrompt.trim();
  const negativePrompt = input.negativePrompt.trim();
  if (videoPrompt.length < 20) throw new AppError("Le prompt vidéo est trop court (20 caractères minimum).");
  if (videoPrompt.length > MAX_PROMPT_CHARS) {
    throw new AppError(`Le prompt vidéo dépasse ${MAX_PROMPT_CHARS} caractères.`);
  }
  if (negativePrompt.length > 600) throw new AppError("Le prompt négatif dépasse 600 caractères.");

  const concept = await db.concept.findFirst({ where: { id: conceptId, project: { userId } } });
  if (!concept) throw new AppError("Concept introuvable.", "NOT_FOUND");

  const original = conceptSchema.safeParse(concept.payload);
  const originalPrompt = original.success ? original.data.videoPrompt : null;
  return db.concept.update({
    where: { id: concept.id },
    data: {
      videoPrompt,
      negativePrompt: negativePrompt || null,
      videoPromptEdited: originalPrompt === null ? true : originalPrompt !== videoPrompt,
    },
  });
}

export type { ConceptPayload };
