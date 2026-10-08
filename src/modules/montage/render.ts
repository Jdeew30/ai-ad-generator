import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { composeCaptionLayer, composeSceneStill } from "./compose";
import { planTimeline } from "./plan";
import { CANVAS, FPS, type MontageSpec, type ScenePlan } from "./types";

export class MontageError extends Error {
  readonly __brand = "MontageError" as const;
  constructor(message: string, readonly canceled = false) {
    super(message);
    this.name = "MontageError";
  }
}

export function isMontageError(error: unknown): error is MontageError {
  return (
    error instanceof MontageError ||
    (typeof error === "object" && error !== null && (error as { __brand?: unknown }).__brand === "MontageError")
  );
}

export interface RenderOptions {
  ffmpegPath: string;
  /** Empty, private directory owned by this job. */
  workDir: string;
  signal?: AbortSignal;
  /** 0..1 */
  onProgress?: (fraction: number) => void;
  /** Called with each spawned process so a caller can kill it. */
  onProcess?: (child: ChildProcess | null) => void;
  /** Per ffmpeg invocation. */
  timeoutMs?: number;
}

const FADE = 0.25;

function runFfmpeg(args: string[], opts: RenderOptions): Promise<void> {
  return new Promise((resolve, reject) => {
    if (opts.signal?.aborted) return reject(new MontageError("Montage annulé.", true));

    const child = spawn(opts.ffmpegPath, ["-hide_banner", "-nostdin", "-loglevel", "error", "-y", ...args], {
      cwd: opts.workDir,
      stdio: ["ignore", "ignore", "pipe"],
      windowsHide: true,
    });
    opts.onProcess?.(child);

    let stderr = "";
    child.stderr.on("data", (chunk: Buffer) => {
      stderr = (stderr + chunk.toString("utf8")).slice(-4000);
    });

    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
      opts.onProcess?.(null);
      fn();
    };

    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(() => reject(new MontageError("Le rendu a dépassé le délai maximum et a été arrêté.")));
    }, opts.timeoutMs ?? 5 * 60_000);

    const onAbort = () => {
      child.kill("SIGKILL");
      finish(() => reject(new MontageError("Montage annulé.", true)));
    };
    opts.signal?.addEventListener("abort", onAbort, { once: true });

    child.on("error", (error: NodeJS.ErrnoException) =>
      finish(() => reject(new MontageError(`Impossible de lancer ffmpeg (${error.code ?? "erreur"}). Vérifiez FFMPEG_PATH ou relancez « npm install ».`))),
    );
    child.on("close", (code) => {
      if (code === 0) return finish(resolve);
      const lastLine = stderr.trim().split(/\r?\n/).filter(Boolean).pop() ?? "erreur inconnue";
      finish(() => reject(new MontageError(`ffmpeg a échoué (code ${code}) : ${lastLine.slice(0, 200)}`)));
    });
  });
}

/** Builds the per-scene filter graph (compatible with ffmpeg 4.x: no xfade). */
export function sceneFilter(scene: ScenePlan, w: number, h: number, hasCaption: boolean): string {
  const d = scene.frames;
  const T = d / FPS;
  const zoomExpr = scene.zoom === "in" ? `1+0.08*on/${d}` : `1.08-0.08*on/${d}`;
  // Keep the background in YUV: round-tripping every frame through RGBA made old ffmpeg builds 4x slower.
  const zoom =
    `[0:v]zoompan=z='${zoomExpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${d}:s=${w}x${h}:fps=${FPS},format=yuv420p[bg]`;
  const tail = `format=yuv420p,fade=t=in:st=0:d=${FADE},fade=t=out:st=${(T - FADE).toFixed(3)}:d=${FADE}[v]`;
  if (!hasCaption) return `${zoom};[bg]${tail}`;
  const capIn = 0.35;
  const capOut = Math.max(capIn + 0.6, T - 0.55);
  const cap = `[1:v]format=rgba,fade=t=in:st=${capIn}:d=0.45:alpha=1,fade=t=out:st=${capOut.toFixed(3)}:d=0.3:alpha=1[cap]`;
  return `${zoom};${cap};[bg][cap]overlay=0:0:format=yuv420,${tail}`;
}

/**
 * Renders the montage to `<workDir>/result.mp4` (H.264, yuv420p, silent, faststart).
 * Each scene is encoded on its own, then the clips are concatenated without
 * re-encoding.
 */
export async function renderMontage(spec: MontageSpec, opts: RenderOptions): Promise<string> {
  if (spec.images.length === 0) throw new MontageError("Ajoutez au moins une photo produit.");

  const { w, h } = CANVAS[spec.aspectRatio];
  const timeline = planTimeline(spec);
  await mkdir(opts.workDir, { recursive: true });

  const clips: string[] = [];
  try {
    for (const scene of timeline) {
      if (opts.signal?.aborted) throw new MontageError("Montage annulé.", true);
      const image = spec.images[scene.imageIndex]!;

      const still = await composeSceneStill(spec.aspectRatio, image, { endCard: scene.isEndCard, logo: scene.isEndCard ? spec.logo : undefined });
      const caption = await composeCaptionLayer(spec.aspectRatio, scene.texts);
      const stillName = `scene_${scene.index}.jpg`;
      const clipName = `clip_${scene.index}.mp4`;
      await writeFile(path.join(opts.workDir, stillName), still);

      const args = ["-i", stillName];
      if (caption) {
        const capName = `caption_${scene.index}.png`;
        await writeFile(path.join(opts.workDir, capName), caption);
        args.push("-loop", "1", "-t", (scene.frames / FPS).toFixed(3), "-i", capName);
      }
      args.push(
        "-filter_complex", sceneFilter(scene, w, h, Boolean(caption)),
        "-map", "[v]",
        "-frames:v", String(scene.frames),
        "-r", String(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        // The pixels are converted with the BT.601 matrix; tag them so players do not assume BT.709.
        "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m", "-color_range", "tv",
        "-an",
        clipName,
      );
      await runFfmpeg(args, opts);
      clips.push(clipName);
      opts.onProgress?.((scene.index + 1) / (timeline.length + 1));
    }

    await writeFile(path.join(opts.workDir, "list.txt"), clips.map((c) => `file '${c}'`).join("\n") + "\n");
    // Never leave a half-written result.mp4 behind: write under another name, then publish.
    await runFfmpeg(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", "result.part"], opts);
    await rename(path.join(opts.workDir, "result.part"), path.join(opts.workDir, "result.mp4"));
    opts.onProgress?.(1);
    return path.join(opts.workDir, "result.mp4");
  } finally {
    // Intermediate files are no longer needed; keep result.mp4 (if any).
    await Promise.allSettled(
      timeline.flatMap((s) => [`scene_${s.index}.jpg`, `caption_${s.index}.png`, `clip_${s.index}.mp4`].map((f) => rm(path.join(opts.workDir, f), { force: true }))),
    );
    await rm(path.join(opts.workDir, "list.txt"), { force: true });
    await rm(path.join(opts.workDir, "result.part"), { force: true });
  }
}

