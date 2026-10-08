import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";

/**
 * Locates a working ffmpeg: FFMPEG_PATH, then the binary bundled by
 * `@ffmpeg-installer/ffmpeg` (installed with `npm install`, all platforms), then
 * whatever `ffmpeg` is on the PATH. The result is verified by actually running
 * `ffmpeg -version` and cached for the life of the process.
 */

let cached: string | null | undefined;

function works(binary: string): boolean {
  try {
    const r = spawnSync(binary, ["-version"], { timeout: 8000, windowsHide: true, stdio: "ignore" });
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

export function resolveFfmpeg(): string | null {
  if (cached !== undefined) return cached;
  const candidates = [process.env.FFMPEG_PATH?.trim() || undefined, bundledPath(), "ffmpeg"].filter(
    (c): c is string => Boolean(c),
  );
  cached = candidates.find(works) ?? null;
  return cached;
}

/** Test seam. */
export function resetFfmpegCache(): void {
  cached = undefined;
}
