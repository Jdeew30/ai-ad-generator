export type DetectedImage = { mime: "image/png" | "image/jpeg" | "image/webp"; ext: "png" | "jpg" | "webp" };

/**
 * Identifies an image by its magic bytes. The client-supplied Content-Type and
 * file name are never trusted. SVG is deliberately unsupported (scriptable → XSS).
 */
export function detectImage(bytes: Uint8Array): DetectedImage | null {
  if (
    bytes.length >= 8 &&
    bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47 &&
    bytes[4] === 0x0d && bytes[5] === 0x0a && bytes[6] === 0x1a && bytes[7] === 0x0a
  ) {
    return { mime: "image/png", ext: "png" };
  }
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
    return { mime: "image/jpeg", ext: "jpg" };
  }
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46 && // RIFF
    bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50 // WEBP
  ) {
    return { mime: "image/webp", ext: "webp" };
  }
  return null;
}

export const MAX_ASSET_BYTES = 8 * 1024 * 1024;
export const MAX_LOGOS_PER_PROJECT = 1;
export const MAX_PRODUCT_IMAGES_PER_PROJECT = 5;

/** Strips path components and control characters; for display only, never for storage paths. */
export function sanitizeDisplayName(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? "image";
  const cleaned = base.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return (cleaned || "image").slice(0, 120);
}
