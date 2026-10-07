/** Max accepted size for a downloaded video (guards disk and bandwidth). */
export const MAX_VIDEO_BYTES = 300 * 1024 * 1024;

export type DetectedVideo = { mime: "video/mp4" | "video/webm" | "video/quicktime" };

/**
 * Identifies a real video container from its first bytes (ISO-BMFF `ftyp`, or
 * Matroska/WebM EBML header). A success is only recorded when this passes — an
 * HTML error page or empty body saved as ".mp4" must never count as a video.
 */
export function detectVideo(head: Uint8Array): DetectedVideo | null {
  if (head.length >= 12 && head[4] === 0x66 && head[5] === 0x74 && head[6] === 0x79 && head[7] === 0x70) {
    // "ftyp"; brand "qt  " denotes QuickTime.
    const brand = String.fromCharCode(head[8]!, head[9]!, head[10]!, head[11]!);
    return { mime: brand === "qt  " ? "video/quicktime" : "video/mp4" };
  }
  if (head.length >= 4 && head[0] === 0x1a && head[1] === 0x45 && head[2] === 0xdf && head[3] === 0xa3) {
    return { mime: "video/webm" };
  }
  return null;
}

export function videoExtension(mime: DetectedVideo["mime"]): string {
  return mime === "video/webm" ? "webm" : mime === "video/quicktime" ? "mov" : "mp4";
}
