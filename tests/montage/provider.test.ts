import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createMontageProvider } from "@/modules/video-providers/montage";
import { isProviderApiError } from "@/modules/video-providers/http";
import type { JobRef, ProviderJobStatus, VideoProvider } from "@/modules/video-providers/types";
import type { MontageSpec } from "@/modules/montage/types";
import { availableFfmpegs, sampleLogo, samplePhoto } from "./helpers";

const ffmpeg = availableFfmpegs()[0]!.path;
let root: string;
beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "adstudio-montage-prov-"));
});
afterEach(() => fs.rmSync(root, { recursive: true, force: true }));

async function spec(over: Partial<MontageSpec> = {}): Promise<MontageSpec> {
  return {
    aspectRatio: "1:1",
    durationSeconds: 4,
    images: [await samplePhoto()],
    logo: await sampleLogo(),
    headline: "Gourde Nomad",
    captions: ["Garde le froid 24 h"],
    callToAction: "Commandez",
    ...over,
  };
}

const ref = (externalId: string): JobRef => ({ externalId, providerData: null, modelId: "montage:photo-slideshow" });

async function submit(provider: VideoProvider, montage: MontageSpec) {
  return provider.submit({ model: provider.models[0]!, prompt: "", aspectRatio: montage.aspectRatio, durationSeconds: montage.durationSeconds, montage });
}

async function until(provider: VideoProvider, job: JobRef, done: (s: ProviderJobStatus) => boolean, timeoutMs = 90_000) {
  const seen: ProviderJobStatus[] = [];
  const start = Date.now();
  for (;;) {
    const status = await provider.poll(job);
    seen.push(status);
    if (done(status)) return { status, seen };
    if (Date.now() - start > timeoutMs) throw new Error(`Timed out; last status ${JSON.stringify(status)}`);
    await new Promise((r) => setTimeout(r, 150));
  }
}

describe("local montage provider", () => {
  it("is explicitly a free, non-AI model", () => {
    const provider = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    const model = provider.models[0]!;
    expect(model.capabilities.montage).toBe(true);
    expect(model.label).toMatch(/gratuit/i);
    expect(model.description).toMatch(/pas de l'IA/i);
    expect(provider.label).toMatch(/sans IA/i);
    expect(provider.isConfigured()).toBe(true);
    expect(provider.cancel).toBeTypeOf("function");
  });

  it("runs a job from queued to a downloadable MP4, with real progress", async () => {
    const provider = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    const { externalId } = await submit(provider, await spec());
    const { status, seen } = await until(provider, ref(externalId), (s) => s.state === "succeeded" || s.state === "failed");

    expect(status.state).toBe("succeeded");
    const states = new Set(seen.map((s) => s.state));
    expect([...states].every((s) => ["queued", "processing", "succeeded"].includes(s))).toBe(true);
    const progresses = seen.flatMap((s) => (s.state === "processing" && s.progress !== undefined ? [s.progress] : []));
    expect(progresses).toEqual([...progresses].sort((a, b) => a - b));

    const res = await provider.download(ref(externalId), status as Extract<ProviderJobStatus, { state: "succeeded" }>);
    const bytes = Buffer.from(await res.arrayBuffer());
    expect(Number(res.headers.get("content-length"))).toBe(bytes.length);
    expect(bytes.subarray(4, 8).toString("ascii")).toBe("ftyp");
    expect(bytes.length).toBeGreaterThan(10_000);
  }, 180_000);

  it("renders one job at a time (the second waits as queued)", async () => {
    const provider = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    const a = await submit(provider, await spec());
    const b = await submit(provider, await spec({ headline: "Second" }));
    const firstB = await provider.poll(ref(b.externalId));
    expect(firstB.state).toBe("queued");
    const [ra, rb] = await Promise.all([
      until(provider, ref(a.externalId), (s) => s.state === "succeeded" || s.state === "failed"),
      until(provider, ref(b.externalId), (s) => s.state === "succeeded" || s.state === "failed"),
    ]);
    expect(ra.status.state).toBe("succeeded");
    expect(rb.status.state).toBe("succeeded");
  }, 240_000);

  it("cancels a running job: reported as canceled, no video is ever offered", async () => {
    const provider = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    const { externalId } = await submit(provider, await spec({ durationSeconds: 12, aspectRatio: "9:16" }));
    await provider.cancel!(ref(externalId));
    const { status } = await until(provider, ref(externalId), (s) => s.state !== "queued" && s.state !== "processing", 30_000);
    expect(status.state).toBe("canceled");
    await new Promise((r) => setTimeout(r, 500));
    expect(await provider.poll(ref(externalId))).toEqual({ state: "canceled" });
    await expect(provider.download(ref(externalId), { state: "succeeded" })).rejects.toThrow();
  }, 60_000);

  it("a queued job that is canceled never starts", async () => {
    const provider = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    const first = await submit(provider, await spec());
    const second = await submit(provider, await spec());
    await provider.cancel!(ref(second.externalId));
    await until(provider, ref(first.externalId), (s) => s.state === "succeeded" || s.state === "failed");
    expect((await provider.poll(ref(second.externalId))).state).toBe("canceled");
    expect(fs.existsSync(path.join(root, second.externalId, "result.mp4"))).toBe(false);
  }, 120_000);

  it("after a restart: a finished job is still served; an interrupted one is reported as interrupted, never as success", async () => {
    const before = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    const done = await submit(before, await spec());
    await until(before, ref(done.externalId), (s) => s.state === "succeeded");

    const after = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg }); // fresh memory = restart
    expect((await after.poll(ref(done.externalId))).state).toBe("succeeded");

    const interruptedId = "11111111-2222-4333-8444-555555555555";
    fs.mkdirSync(path.join(root, interruptedId));
    const interrupted = await after.poll(ref(interruptedId));
    expect(interrupted).toMatchObject({ state: "failed", message: expect.stringContaining("interrompu") });

    const unknown = await after.poll(ref("99999999-2222-4333-8444-555555555555"));
    expect(unknown).toMatchObject({ state: "failed", message: expect.stringContaining("introuvable") });
  }, 120_000);

  it("rejects job ids that could escape the work folder", async () => {
    const provider = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    for (const bad of ["../../etc", "..", "a/b", "", "11111111-2222-4333-8444-5555555555555"]) {
      const error = await provider.poll(ref(bad)).catch((e: unknown) => e);
      expect(isProviderApiError(error), bad).toBe(true);
      await expect(provider.download(ref(bad), { state: "succeeded" })).rejects.toThrow();
    }
  });

  it("refuses to run without ffmpeg, and without a spec", async () => {
    const none = createMontageProvider({ workRoot: root, ffmpegPath: () => null });
    expect(none.isConfigured()).toBe(false);
    await expect(submit(none, await spec())).rejects.toMatchObject({ code: "NOT_CONFIGURED" });
    const ok = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    await expect(ok.submit({ model: ok.models[0]!, prompt: "", aspectRatio: "1:1", durationSeconds: 4 })).rejects.toThrow(/photos/);
  });

  it("reports a render failure as failed with a reason", async () => {
    const provider = createMontageProvider({ workRoot: root, ffmpegPath: () => ffmpeg });
    const { externalId } = await submit(provider, await spec({ images: [new Uint8Array([9, 9, 9])] }));
    const { status } = await until(provider, ref(externalId), (s) => s.state === "succeeded" || s.state === "failed", 30_000);
    expect(status.state).toBe("failed");
  }, 60_000);
});
