import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";

/**
 * Locates a working ffmpeg: FFMPEG_PATH, then the binary bundled by
 * `@ffmpeg-installer/ffmpeg` (installed with `npm install`, all platforms), then
 * whatever `ffmpeg` is on the PATH. The result is verified by actually running
 * `ffmpeg -version`. A positive result is cached for the life of the process; a
 * negative one only briefly, so a slow first start (antivirus scan of a large .exe,
 * network drive) cannot disable the feature until the next restart.
 */

const NEGATIVE_TTL_MS = 30_000;
let cached: { path: string | null; at: number } | undefined;

function works(binary: string): boolean {
  try {
    const r = spawnSync(binary, ["-version"], { timeout: 15_000, windowsHide: true, stdio: "ignore" });
    return r.status === 0;
  } catch {
    return false;
  }
}

function bundledPath(): string | undefined {
  try {
    // Resolved from the project root at runtime so bundlers never inline the binary.
    const req = createRequire(path.join(process.cwd(), "package.json"));
    const mod = req("@ffmpeg-installer/ffmpeg") as { path?: string };
    return mod.path;
  } catch {
    return undefined;
  }
}

/**
 * A path with a directory part is made absolute: ffmpeg is spawned from another
 * working directory (the job folder), where a relative path would not resolve.
 */
export function absolutizeBinary(candidate: string): string {
  return /[\\/]/.test(candidate) ? path.resolve(candidate) : candidate;
}

export function resolveFfmpeg(): string | null {
  if (cached && (cached.path !== null || Date.now() - cached.at < NEGATIVE_TTL_MS)) return cached.path;
  const candidates = [process.env.FFMPEG_PATH?.trim() || undefined, bundledPath(), "ffmpeg"]
    .filter((c): c is string => Boolean(c))
    .map(absolutizeBinary);
  cached = { path: candidates.find(works) ?? null, at: Date.now() };
  return cached.path;
}

/** Test seam. */
export function resetFfmpegCache(): void {
  cached = undefined;
}
