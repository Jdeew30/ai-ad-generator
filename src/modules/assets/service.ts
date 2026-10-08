import { randomUUID } from "node:crypto";
import sharp from "sharp";
import type { Db } from "@/lib/db";
import { AppError } from "@/lib/errors";
import { getStorage } from "@/modules/storage";
import {
  MAX_ASSET_BYTES,
  MAX_ASSET_PIXELS,
  MAX_LOGOS_PER_PROJECT,
  MAX_PRODUCT_IMAGES_PER_PROJECT,
  detectImage,
  sanitizeDisplayName,
} from "./image";

export type AssetKindInput = "LOGO" | "PRODUCT_IMAGE";

export async function addAsset(
  db: Db,
  userId: string,
  input: { projectId: string; kind: AssetKindInput; originalName: string; bytes: Buffer },
) {
  const project = await db.project.findFirst({ where: { id: input.projectId, userId }, select: { id: true } });
  if (!project) throw new AppError("Projet introuvable.", "NOT_FOUND");

  if (input.bytes.length === 0) throw new AppError("Fichier vide.");
  if (input.bytes.length > MAX_ASSET_BYTES) {
    throw new AppError(`Fichier trop volumineux (maximum ${MAX_ASSET_BYTES / 1024 / 1024} Mo).`);
  }
  const image = detectImage(input.bytes);
  if (!image) throw new AppError("Format non supporté. Utilisez une image PNG, JPEG ou WebP.");

  // Header-only read: refuse images whose decoded size would be abusive, before storing them.
  let pixels = 0;
  try {
    const meta = await sharp(input.bytes).metadata();
    pixels = (meta.width ?? 0) * (meta.height ?? 0);
  } catch {
    throw new AppError("Image illisible ou corrompue.");
  }
  if (pixels === 0) throw new AppError("Image illisible ou corrompue.");
  if (pixels > MAX_ASSET_PIXELS) {
    throw new AppError(`Image trop grande (${Math.round(pixels / 1_000_000)} mégapixels, maximum ${MAX_ASSET_PIXELS / 1_000_000}). Réduisez-la avant l'envoi.`);
  }

  const max = input.kind === "LOGO" ? MAX_LOGOS_PER_PROJECT : MAX_PRODUCT_IMAGES_PER_PROJECT;
  const existing = await db.asset.count({ where: { projectId: project.id, kind: input.kind } });
  if (existing >= max) {
    throw new AppError(
      input.kind === "LOGO"
        ? "Un seul logo par projet. Supprimez l'actuel pour en ajouter un autre."
        : `${max} images produit maximum par projet.`,
    );
  }

  const storageKey = `assets/${project.id}/${randomUUID()}.${image.ext}`;
  const storage = getStorage();
  await storage.put(storageKey, input.bytes, { maxBytes: MAX_ASSET_BYTES });

  try {
    return await db.asset.create({
      data: {
        projectId: project.id,
        kind: input.kind,
        storageKey,
        mimeType: image.mime,
        sizeBytes: input.bytes.length,
        originalName: sanitizeDisplayName(input.originalName),
      },
    });
  } catch (error) {
    await storage.delete(storageKey);
    throw error;
  }
}

export async function getAssetForUser(db: Db, userId: string, assetId: string) {
  const asset = await db.asset.findFirst({
    where: { id: assetId, project: { userId } },
  });
  if (!asset) throw new AppError("Fichier introuvable.", "NOT_FOUND");
  return asset;
}

export async function deleteAsset(db: Db, userId: string, assetId: string) {
  const asset = await getAssetForUser(db, userId, assetId);
  await db.asset.delete({ where: { id: asset.id } });
  await getStorage().delete(asset.storageKey);
}
