import fs from "node:fs";
import { beforeEach, afterEach, describe, expect, it } from "vitest";
import { getDb } from "@/lib/db";
import { getEnv, resetEnvCache } from "@/lib/env";
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
