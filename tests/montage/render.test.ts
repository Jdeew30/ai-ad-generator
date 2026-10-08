import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { absolutizeBinary, resetFfmpegCache, resolveFfmpeg } from "@/modules/montage/ffmpeg";
import { isMontageError, renderMontage, sceneFilter } from "@/modules/montage/render";
import type { MontageSpec } from "@/modules/montage/types";
import { availableFfmpegs, frameAt, probe, probeText, sampleLogo, samplePhoto, whiteShare } from "./helpers";

const ffmpegs = availableFfmpegs();
let dir: string;
beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "adstudio-montage-test-"));
});
afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

async function spec(over: Partial<MontageSpec> = {}): Promise<MontageSpec> {
  return {
    aspectRatio: "9:16",
    durationSeconds: 5,
    images: [await samplePhoto()],
    logo: await sampleLogo(),
    headline: "iPhone 18",
    captions: ["Écran lumineux", "Photo nocturne"],
    callToAction: "Découvrez-le",
    ...over,
  };
}

it("finds at least one working ffmpeg (bundled or system)", () => {
  expect(ffmpegs.length).toBeGreaterThan(0);
});

describe.each(ffmpegs)("render with $name ffmpeg", ({ path: ffmpegPath }) => {
  it("produces a real, correctly sized H.264 MP4 of the requested length, silent, intermediates removed", async () => {
    const file = await renderMontage(await spec(), { ffmpegPath, workDir: dir });
    const head = fs.readFileSync(file).subarray(4, 8).toString("ascii");
    expect(head).toBe("ftyp");
    const p = probe(ffmpegPath, file);
    expect(p).toMatchObject({ codec: "h264", pixFmt: "yuv420p", width: 1080, height: 1920, hasAudio: false });
    expect(p.duration).toBeGreaterThan(4.9);
    expect(p.duration).toBeLessThan(5.15);
    expect(fs.readdirSync(dir)).toEqual(["result.mp4"]);
  }, 180_000);

  it("tags the colour space so players decode the colours correctly (no BT.709 guess)", async () => {
    const file = await renderMontage(await spec({ durationSeconds: 4 }), { ffmpegPath, workDir: dir });
    expect(probeText(ffmpegPath, file)).toMatch(/smpte170m/);
  }, 180_000);

  it("actually draws the text: dark at the first frame, white title text mid-scene, white pill on the end card", async () => {
    const file = await renderMontage(await spec(), { ffmpegPath, workDir: dir });
    const first = await frameAt(ffmpegPath, file, 0);
    const title = await frameAt(ffmpegPath, file, 1.2);
    const end = await frameAt(ffmpegPath, file, 4.3);
    // t=0 is the fade-in from black.
    const mean = first.data.reduce((s, v) => s + v, 0) / first.data.length;
    expect(mean).toBeLessThan(25);
    // Title scene: white glyphs sit in the lower text area.
    expect(whiteShare(title, 0.78, 0.92)).toBeGreaterThan(0.004);
    // End card: the white CTA pill + logo plate are large white areas.
    expect(whiteShare(end, 0.55, 0.9)).toBeGreaterThan(whiteShare(title, 0.55, 0.9));
    expect(whiteShare(end, 0.2, 0.5)).toBeGreaterThan(0.05); // logo plate
  }, 180_000);
});

describe("formats and inputs (resolved ffmpeg)", () => {
  const ffmpegPath = ffmpegs[0]!.path;

  it.each([
    ["16:9", 1920, 1080, 4],
    ["1:1", 1080, 1080, 6],
  ] as const)("%s renders at %dx%d for %d s", async (aspectRatio, width, height, seconds) => {
    const file = await renderMontage(await spec({ aspectRatio, durationSeconds: seconds }), { ffmpegPath, workDir: dir });
    const p = probe(ffmpegPath, file);
    expect([p.width, p.height]).toEqual([width, height]);
    expect(Math.abs(p.duration - seconds)).toBeLessThan(0.15);
  }, 180_000);

  it("works with several photos of different shapes, no logo and no call to action", async () => {
    const file = await renderMontage(
      await spec({
        aspectRatio: "9:16",
        durationSeconds: 8,
        images: [await samplePhoto("#336699", 400, 1200), await samplePhoto("#993366", 1600, 400), await samplePhoto("#669933", 800, 800)],
        logo: undefined,
        callToAction: undefined,
        captions: [],
      }),
      { ffmpegPath, workDir: dir },
    );
    expect(Math.abs(probe(ffmpegPath, file).duration - 8)).toBeLessThan(0.15);
  }, 180_000);

  it("survives hostile text: tags, emoji, very long words", async () => {
    const file = await renderMontage(
      await spec({
        headline: "<b>\"; rm -rf / 🔥' && echo pwned</b>",
        captions: ["💥💥💥", "Supercalifragilisticexpialidocious".repeat(8)],
        callToAction: "$(whoami) `id` %{pts} \\ ' \"",
      }),
      { ffmpegPath, workDir: dir },
    );
    expect(fs.existsSync(file)).toBe(true);
  }, 180_000);

  it("rejects a spec without photos", async () => {
    await expect(renderMontage(await spec({ images: [] }), { ffmpegPath, workDir: dir })).rejects.toThrow(/photo produit/);
  });

  it("fails cleanly (and leaves no result) when an image is corrupt", async () => {
    await expect(
      renderMontage(await spec({ images: [new Uint8Array([1, 2, 3, 4])] }), { ffmpegPath, workDir: dir }),
    ).rejects.toThrow();
    expect(fs.existsSync(path.join(dir, "result.mp4"))).toBe(false);
  });

  it("reports an unusable ffmpeg binary", async () => {
    const error = await renderMontage(await spec(), { ffmpegPath: path.join(dir, "no-such-ffmpeg"), workDir: dir }).catch((e: unknown) => e);
    expect(isMontageError(error)).toBe(true);
    expect((error as Error).message).toMatch(/lancer ffmpeg/);
    // No server path in a message that reaches the user.
    expect((error as Error).message).not.toContain(dir);
    expect((error as Error).message).not.toMatch(/no-such-ffmpeg/);
  });

  it("stops promptly when aborted and reports a cancellation", async () => {
    const controller = new AbortController();
    const started = Date.now();
    const pending = renderMontage(await spec({ durationSeconds: 12 }), { ffmpegPath, workDir: dir, signal: controller.signal });
    setTimeout(() => controller.abort(), 700);
    const error = await pending.catch((e: unknown) => e);
    expect(isMontageError(error) && error.canceled).toBe(true);
    expect(Date.now() - started).toBeLessThan(20_000);
    expect(fs.existsSync(path.join(dir, "result.mp4"))).toBe(false);
    expect(fs.existsSync(path.join(dir, "result.part"))).toBe(false);
  }, 60_000);

  it("kills a render that exceeds its time limit", async () => {
    const error = await renderMontage(await spec({ durationSeconds: 12 }), { ffmpegPath, workDir: dir, timeoutMs: 50 }).catch((e: unknown) => e);
    expect((error as Error).message).toMatch(/délai maximum/);
  }, 60_000);

  it("reports progress up to 100%", async () => {
    const seen: number[] = [];
    await renderMontage(await spec({ durationSeconds: 5 }), { ffmpegPath, workDir: dir, onProgress: (f) => seen.push(f) });
    expect(seen.at(-1)).toBe(1);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
  }, 180_000);
});

describe("filter graph (ffmpeg 4.x compatible)", () => {
  const scene = { index: 0, imageIndex: 0, frames: 90, zoom: "in" as const, texts: [], isEndCard: false };
  it("only uses filters present in old ffmpeg builds (no xfade) and never injects user text", () => {
    const g = sceneFilter(scene, 1080, 1920, true);
    expect(g).toContain("zoompan");
    expect(g).toContain("overlay");
    expect(g).not.toContain("xfade");
    expect(sceneFilter({ ...scene, zoom: "out" }, 1080, 1920, false)).not.toContain("overlay");
  });
});

describe("resolveFfmpeg", () => {
  afterEach(() => {
    delete process.env.FFMPEG_PATH;
    resetFfmpegCache();
  });
  it("ignores an invalid FFMPEG_PATH and falls back to a working binary", () => {
    process.env.FFMPEG_PATH = "/definitely/not/ffmpeg";
    resetFfmpegCache();
    const found = resolveFfmpeg();
    expect(found).not.toBeNull();
    expect(found).not.toBe("/definitely/not/ffmpeg");
  });
  it("honours a valid FFMPEG_PATH first", () => {
    const system = ffmpegs.find((f) => f.name === "system");
    if (!system) return;
    process.env.FFMPEG_PATH = system.path;
    resetFfmpegCache();
    expect(resolveFfmpeg()).toBe(system.path);
  });
});

describe("absolutizeBinary (a relative FFMPEG_PATH must still work from the job folder)", () => {
  it("keeps bare command names, resolves anything with a directory part", () => {
    expect(absolutizeBinary("ffmpeg")).toBe("ffmpeg");
    expect(path.isAbsolute(absolutizeBinary("./tools/ffmpeg"))).toBe(true);
    expect(path.isAbsolute(absolutizeBinary("tools/ffmpeg"))).toBe(true);
    expect(path.isAbsolute(absolutizeBinary("..\\bin\\ffmpeg.exe"))).toBe(true);
    expect(absolutizeBinary("/usr/bin/ffmpeg")).toBe(path.resolve("/usr/bin/ffmpeg"));
  });

  it("a relative FFMPEG_PATH resolves to a binary that really renders from another directory", async () => {
    const abs = ffmpegs.find((f) => path.isAbsolute(f.path))?.path;
    if (!abs) return;
    process.env.FFMPEG_PATH = path.relative(process.cwd(), abs);
    resetFfmpegCache();
    try {
      const resolved = resolveFfmpeg();
      expect(resolved && path.isAbsolute(resolved)).toBe(true);
      const file = await renderMontage(await spec({ durationSeconds: 4 }), { ffmpegPath: resolved!, workDir: dir });
      expect(fs.existsSync(file)).toBe(true);
    } finally {
      delete process.env.FFMPEG_PATH;
      resetFfmpegCache();
    }
  }, 180_000);
});
