import sharp from "sharp";
import type { ReferenceImage } from "@/modules/video-providers/types";

const DEFAULT_MAX_EDGE = 1536;

/**
 * Normalises a user image into something safe and small to send to an external
 * API (video provider or LLM vision): EXIF-rotated, flattened on white (no
 * alpha), longest edge capped, re-encoded as JPEG. Keeps payloads well under
 * provider limits. Throws on undecodable input or images with an absurd pixel count.
 */
export async function prepareReferenceImage(input: Uint8Array, maxEdge = DEFAULT_MAX_EDGE): Promise<ReferenceImage> {
  return withDecodeSlot(() => prepare(input, maxEdge));
}

/** At most two full image decodes at a time: a big decode can take hundreds of MB of memory. */
const MAX_CONCURRENT_DECODES = 2;
let activeDecodes = 0;
const waiting: Array<() => void> = [];

async function withDecodeSlot<T>(task: () => Promise<T>): Promise<T> {
  if (activeDecodes >= MAX_CONCURRENT_DECODES) await new Promise<void>((resolve) => waiting.push(resolve));
  activeDecodes += 1;
  try {
    return await task();
  } finally {
    activeDecodes -= 1;
    waiting.shift()?.();
  }
}

async function prepare(input: Uint8Array, maxEdge: number): Promise<ReferenceImage> {
  const out = await sharp(input, { limitInputPixels: 50_000_000 })
    .rotate()
    .flatten({ background: "#ffffff" })
    .resize({ width: maxEdge, height: maxEdge, fit: "inside", withoutEnlargement: true })
    .jpeg({ quality: 88, mozjpeg: true })
    .toBuffer();
  return { bytes: out, mimeType: "image/jpeg" };
}
