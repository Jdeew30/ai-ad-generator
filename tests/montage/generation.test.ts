import fs from "node:fs";
import { beforeEach, afterEach, describe, expect, it } from "vitest";
import { getDb } from "@/lib/db";
import { getEnv, resetEnvCache } from "@/lib/env";
import { toGenerationDto } from "@/modules/generations/dto";
import {
  cancelGeneration,
  getDownloadableVideo,
  getQuotaUsage,
  startGeneration,
  syncGeneration,
} from "@/modules/generations/service";
import { getStorage } from "@/modules/storage";
import { buildProviders, setProvidersForTests } from "@/modules/video-providers";
import { createProject, createUser, resetDb } from "../helpers/db";
import { sampleLogo, samplePhoto } from "./helpers";

const db = getDb();
const MONTAGE = "montage:photo-slideshow";

beforeEach(async () => {
  await resetDb();
  resetEnvCache();
  setProvidersForTests(buildProviders(getEnv())); // real providers: montage uses the real ffmpeg
});
afterEach(() => {
  setProvidersForTests(undefined);
  resetEnvCache();
});

async function addAsset(projectId: string, kind: "PRODUCT_IMAGE" | "LOGO", bytes: Buffer) {
  const storageKey = `assets/${projectId}/${kind}-${Math.random().toString(16).slice(2)}.jpg`;
  await getStorage().put(storageKey, bytes, { maxBytes: 20_000_000 });
  return db.asset.create({ data: { projectId, kind, storageKey, mimeType: "image/jpeg", sizeBytes: bytes.length, originalName: "x.jpg" } });
}

async function finish(id: string, timeoutMs = 120_000) {
  const start = Date.now();
  for (;;) {
    const g = (await syncGeneration(db, id, { force: true }))!;
    if (g.status === "SUCCEEDED" || g.status === "FAILED" || g.status === "CANCELED") return g;
    if (Date.now() - start > timeoutMs) throw new Error(`stuck in ${g.status}`);
    await new Promise((r) => setTimeout(r, 300));
  }
}

describe("free montage through the real generation lifecycle", () => {
  it("needs no concept and no API key; ends SUCCEEDED with a verified MP4 in storage", async () => {
    const user = await createUser();
    const project = await createProject(user.id, { aspectRatio: "1:1", durationSeconds: 5 });
    await db.project.update({ where: { id: project.id }, data: { callToAction: "Commandez" } });
    await addAsset(project.id, "PRODUCT_IMAGE", await samplePhoto());
    await addAsset(project.id, "LOGO", await sampleLogo());
    expect(await db.concept.count()).toBe(0);

    const started = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 5 });
    expect(started.status).toBe("PROCESSING");
    expect(started.providerId).toBe("montage");
    expect(started.conceptId).toBeNull();
    expect(started.prompt).toContain("Montage automatique (sans IA");
    expect(started.prompt).toContain("Gourde Nomad");
    expect(started.prompt).toContain("Garde le froid 24h");

    const done = await finish(started.id);
    expect(done.status).toBe("SUCCEEDED");
    expect(done.outputMimeType).toBe("video/mp4");
    expect(done.outputBytes).toBeGreaterThan(10_000);

    const video = await getDownloadableVideo(db, user.id, done.id);
    const head = Buffer.alloc(12);
    const fd = fs.openSync(`${process.env.STORAGE_DIR}/${video.key}`, "r");
    fs.readSync(fd, head, 0, 12, 0);
    fs.closeSync(fd);
    expect(head.subarray(4, 8).toString("ascii")).toBe("ftyp");
  }, 180_000);

  it("refuses to start without a product photo, and records nothing", async () => {
    const user = await createUser();
    const project = await createProject(user.id);
    await expect(startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 5 })).rejects.toThrow(/photo produit/);
    await addAsset(project.id, "LOGO", await sampleLogo()); // a logo alone is not enough
    await expect(startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 5 })).rejects.toThrow(/photo produit/);
    expect(await db.generation.count()).toBe(0);
  });

  it("is free: it does not consume the paid daily quota, and is not blocked by it", async () => {
    process.env.DAILY_GENERATION_LIMIT = "1";
    resetEnvCache();
    setProvidersForTests(buildProviders(getEnv()));
    try {
      const user = await createUser();
      const project = await createProject(user.id, { aspectRatio: "1:1" });
      await addAsset(project.id, "PRODUCT_IMAGE", await samplePhoto());
      // A paid generation already used today's whole quota.
      await db.generation.create({
        data: { projectId: project.id, userId: user.id, providerId: "replicate", modelId: "replicate:bytedance/seedance-1-lite", prompt: "p", aspectRatio: "1:1", durationSeconds: 5, status: "SUCCEEDED", externalId: "x" },
      });
      expect((await getQuotaUsage(db, user.id)).used).toBe(1);

      const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 4 });
      expect(g.status).toBe("PROCESSING");
      await finish(g.id);
      expect((await getQuotaUsage(db, user.id)).used).toBe(1); // unchanged by the montage
    } finally {
      process.env.DAILY_GENERATION_LIMIT = "5";
    }
  }, 180_000);

  it("can be canceled while running", async () => {
    const user = await createUser();
    const project = await createProject(user.id, { aspectRatio: "9:16" });
    await addAsset(project.id, "PRODUCT_IMAGE", await samplePhoto());
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 12 });
    const canceled = await cancelGeneration(db, user.id, g.id);
    expect(canceled.status).toBe("CANCELED");
    expect((await syncGeneration(db, g.id, { force: true }))!.status).toBe("CANCELED");
    expect(canceled.outputKey).toBeNull();
  }, 60_000);

  it("another user's project stays out of reach", async () => {
    const owner = await createUser();
    const intruder = await createUser();
    const project = await createProject(owner.id, { aspectRatio: "1:1" });
    await addAsset(project.id, "PRODUCT_IMAGE", await samplePhoto());
    await expect(startGeneration(db, intruder.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 5 })).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("a corrupt photo fails the generation with a reason instead of a fake success", async () => {
    const user = await createUser();
    const project = await createProject(user.id, { aspectRatio: "1:1" });
    await addAsset(project.id, "PRODUCT_IMAGE", Buffer.from("this is not an image"));
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 4 });
    expect(g.status).toBe("FAILED");
    expect(g.errorMessage).toMatch(/photo produit/);
    expect(g.outputKey).toBeNull();
  });
});

describe("montage: review findings (regression)", () => {
  async function projectWithPhotos(photos: number, over: Parameters<typeof createProject>[1] = {}) {
    const user = await createUser();
    const project = await createProject(user.id, { aspectRatio: "1:1", ...over });
    await db.project.update({
      where: { id: project.id },
      data: { benefits: ["Premier avantage", "Deuxième avantage", "Troisième avantage"], callToAction: "Découvrez-le" },
    });
    for (let i = 0; i < photos; i++) await addAsset(project.id, "PRODUCT_IMAGE", await samplePhoto(`#${(i * 3 + 2).toString(16).repeat(6).slice(0, 6)}`));
    return { user, project };
  }

  it("the stored description tells what the video really contains (not the raw spec)", async () => {
    const { user, project } = await projectWithPhotos(5);
    await addAsset(project.id, "LOGO", await sampleLogo());
    const short = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 5 });
    expect(short.prompt).toContain("2 photo(s) utilisée(s) sur 5");
    expect(short.prompt).toContain("« Premier avantage »");
    expect(short.prompt).not.toContain("Troisième avantage");
    expect(short.prompt).toContain("logo affiché");
    await cancelGeneration(db, user.id, short.id);

    const long = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 10 });
    expect(long.prompt).toContain("5 photo(s) utilisée(s) sur 5");
    for (const b of ["Premier", "Deuxième", "Troisième"]) expect(long.prompt).toContain(`« ${b} avantage »`);
    await cancelGeneration(db, user.id, long.id);
  });

  it("is not linked to an AI concept, even when the project has one", async () => {
    const { user, project } = await projectWithPhotos(1);
    await db.concept.create({ data: { projectId: project.id, version: 1, source: "TEMPLATE", payload: {}, videoPrompt: "x".repeat(40) } });
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 4 });
    expect(g.conceptId).toBeNull();
    expect(toGenerationDto(g).montage).toBe(true);
    await cancelGeneration(db, user.id, g.id);
  });

  it("a finished montage collected after the 45-minute limit is still a success", async () => {
    const { user, project } = await projectWithPhotos(1);
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 4 });
    // Let the render finish without anyone collecting it...
    const provider = buildProviders(getEnv()); // (separate instance: only to prove nothing else is needed)
    void provider;
    const start = Date.now();
    for (;;) {
      const row = (await db.generation.findUnique({ where: { id: g.id } }))!;
      await new Promise((r) => setTimeout(r, 400));
      const files = fs.existsSync(`${process.env.STORAGE_DIR}/.montage-tmp/${row.externalId}/result.mp4`);
      if (files) break;
      if (Date.now() - start > 90_000) throw new Error("render never finished");
    }
    // ...then collect it 46 minutes late: the ready result must win over the age limit.
    const late = new Date(Date.now() + 46 * 60_000);
    const done = (await syncGeneration(db, g.id, { force: true, now: late }))!;
    expect(done.status).toBe("SUCCEEDED");
    expect(done.outputKey).not.toBeNull();
  }, 150_000);

  it("removes the temporary render folder once the video is stored", async () => {
    const { user, project } = await projectWithPhotos(1);
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 4 });
    const done = await finish(g.id);
    expect(done.status).toBe("SUCCEEDED");
    expect(fs.existsSync(`${process.env.STORAGE_DIR}/.montage-tmp/${g.externalId}`)).toBe(false);
    // the stored copy remains available
    expect((await getDownloadableVideo(db, user.id, g.id)).size).toBeGreaterThan(10_000);
  }, 150_000);

  it("a canceled montage leaves no render folder behind", async () => {
    const { user, project } = await projectWithPhotos(2, { aspectRatio: "9:16" });
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MONTAGE, durationSeconds: 12 });
    await cancelGeneration(db, user.id, g.id);
    await new Promise((r) => setTimeout(r, 1500));
    expect(fs.existsSync(`${process.env.STORAGE_DIR}/.montage-tmp/${g.externalId}`)).toBe(false);
  }, 60_000);
});
