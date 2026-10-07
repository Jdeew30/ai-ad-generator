import { randomUUID } from "node:crypto";
import sharp from "sharp";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { getDb } from "@/lib/db";
import { getEnv, resetEnvCache } from "@/lib/env";
import { AppError } from "@/lib/errors";
import {
  cancelGeneration,
  getDownloadableVideo,
  getGenerationForUser,
  startGeneration,
  syncActiveGenerations,
  syncGeneration,
} from "@/modules/generations/service";
import { getStorage } from "@/modules/storage";
import { buildProviders, setProvidersForTests } from "@/modules/video-providers";
import { createConcept, createProject, createUser, resetDb } from "./helpers/db";
import { fakeMp4, startStubServer, type StubServer, type StubHandler } from "./helpers/stub-server";

/**
 * Integration: real PostgreSQL + real local storage + the REAL provider adapters,
 * talking HTTP to a local test double. This proves our lifecycle logic; it does
 * not (and cannot) prove the live provider APIs — see README "État réel".
 */

const db = getDb();
const MODEL = "replicate:bytedance/seedance-1-lite";
const SORA = "openai:sora-2";

let server: StubServer;
/** Mutable behaviour of the fake Replicate. */
let replicate: { predictionStatus: unknown; downloadResponse: () => ReturnType<StubHandler>; downloads: number };

function useProviders(extra: Partial<Record<string, string>> = {}) {
  resetEnvCache();
  const env = {
    ...getEnv(),
    REPLICATE_API_TOKEN: "r8_test",
    REPLICATE_BASE_URL: server.url,
    ...extra,
  };
  setProvidersForTests(buildProviders(env, { urlPolicy: "any" }));
}

beforeEach(async () => {
  await resetDb();
  replicate = {
    predictionStatus: { id: "pred_1", status: "starting" },
    downloadResponse: () => ({ body: fakeMp4(8192), headers: { "content-type": "video/mp4" } }),
    downloads: 0,
  };
  server = await startStubServer((req) => {
    if (req.method === "POST" && req.path === "/models/bytedance/seedance-1-lite/predictions") {
      return { status: 201, json: { id: "pred_1", status: "starting" } };
    }
    if (req.method === "GET" && req.path === "/predictions/pred_1") return { json: replicate.predictionStatus };
    if (req.method === "POST" && req.path === "/predictions/pred_1/cancel") return { json: { id: "pred_1", status: "canceled" } };
    if (req.path === "/out.mp4") {
      replicate.downloads += 1;
      return replicate.downloadResponse();
    }
    return { status: 404, json: { detail: "not found" } };
  });
  useProviders();
});

afterEach(async () => {
  await server.close();
  setProvidersForTests(undefined);
  resetEnvCache();
});

async function setupProject(overrides?: Parameters<typeof createProject>[1]) {
  const user = await createUser();
  const project = await createProject(user.id, overrides);
  await createConcept(project.id);
  return { user, project };
}

const succeeded = () => ({ id: "pred_1", status: "succeeded", output: `${server.url}/out.mp4` });

describe("startGeneration", () => {
  it("records a PROCESSING job only after the provider accepted it", async () => {
    const { user, project } = await setupProject();
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 });

    expect(g.status).toBe("PROCESSING");
    expect(g.externalId).toBe("pred_1");
    expect(g.providerId).toBe("replicate");
    expect(g.prompt).toContain("water bottle");
    expect(g.outputKey).toBeNull();
    const post = server.requests.find((r) => r.method === "POST")!;
    expect(post.headers.authorization).toBe("Bearer r8_test");
  });

  it("records a FAILED job (never PROCESSING) when the provider rejects the request", async () => {
    server.setHandler(() => ({ status: 422, json: { detail: "duration: invalid" } }));
    const { user, project } = await setupProject();
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 });

    expect(g.status).toBe("FAILED");
    expect(g.externalId).toBeNull();
    expect(g.errorMessage).toBe("duration: invalid");
  });

  it("refuses a provider that has no credentials, without creating any row", async () => {
    const { user, project } = await setupProject();
    await expect(
      startGeneration(db, user.id, { projectId: project.id, modelId: "fal:fal-ai/kling-video/v2.1/master", durationSeconds: 5 }),
    ).rejects.toMatchObject({ code: "NOT_CONFIGURED", message: expect.stringContaining("FAL_KEY") });
    expect(await db.generation.count()).toBe(0);
  });

  it("refuses combinations the model does not declare (duration / format)", async () => {
    useProviders({ OPENAI_API_KEY: "sk-test", OPENAI_BASE_URL: server.url });
    const { user, project } = await setupProject({ aspectRatio: "1:1" });
    await expect(
      startGeneration(db, user.id, { projectId: project.id, modelId: SORA, durationSeconds: 4 }),
    ).rejects.toThrow(/format 1:1/);

    const { user: u2, project: p2 } = await setupProject({ aspectRatio: "9:16" });
    await expect(
      startGeneration(db, u2.id, { projectId: p2.id, modelId: SORA, durationSeconds: 10 }),
    ).rejects.toThrow(/10 s/);
    expect(await db.generation.count()).toBe(0);
  });

  it("requires a concept", async () => {
    const user = await createUser();
    const project = await createProject(user.id);
    await expect(
      startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 }),
    ).rejects.toThrow(/concept/);
  });

  it("cannot be started on another user's project", async () => {
    const { project } = await setupProject();
    const intruder = await createUser();
    await expect(
      startGeneration(db, intruder.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 }),
    ).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("enforces the daily quota but does not count jobs the provider never accepted", async () => {
    const { user, project } = await setupProject();
    // 3 failed-at-submit jobs: free.
    server.setHandler(() => ({ status: 400, json: { detail: "bad" } }));
    for (let i = 0; i < 3; i++) {
      await startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 });
    }
    // 5 accepted jobs (quota = 5), finishing each so the active-jobs cap is not hit.
    server.setHandler((req) =>
      req.method === "POST" ? { status: 201, json: { id: "pred_1", status: "starting" } } : { json: {} },
    );
    for (let i = 0; i < 5; i++) {
      const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 });
      expect(g.status).toBe("PROCESSING");
      await db.generation.update({ where: { id: g.id }, data: { status: "FAILED" } });
    }
    await expect(
      startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 }),
    ).rejects.toMatchObject({ code: "QUOTA", message: expect.stringContaining("Limite quotidienne") });
  });

  it("caps concurrent in-flight jobs per user", async () => {
    const { user, project } = await setupProject();
    for (let i = 0; i < 3; i++) {
      await startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 });
    }
    await expect(
      startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 }),
    ).rejects.toMatchObject({ code: "QUOTA", message: expect.stringContaining("en cours") });
  });

  it("holds the quota under concurrent starts (advisory lock)", async () => {
    process.env.DAILY_GENERATION_LIMIT = "2";
    resetEnvCache();
    useProviders();
    try {
      const { user, project } = await setupProject();
      const results = await Promise.allSettled(
        Array.from({ length: 6 }, () =>
          startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 }),
        ),
      );
      expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(2);
      expect(await db.generation.count({ where: { userId: user.id } })).toBe(2);
    } finally {
      process.env.DAILY_GENERATION_LIMIT = "5";
      resetEnvCache();
    }
  });

  it("sends a normalised JPEG data URI when a product image is used as reference", async () => {
    const { user, project } = await setupProject();
    const png = await sharp({ create: { width: 64, height: 64, channels: 3, background: "#ff0000" } }).png().toBuffer();
    const storageKey = `assets/${project.id}/${randomUUID()}.png`;
    await getStorage().put(storageKey, png, { maxBytes: 1_000_000 });
    const asset = await db.asset.create({
      data: { projectId: project.id, kind: "PRODUCT_IMAGE", storageKey, mimeType: "image/png", sizeBytes: png.length, originalName: "p.png" },
    });

    const g = await startGeneration(db, user.id, {
      projectId: project.id,
      modelId: MODEL,
      durationSeconds: 5,
      referenceAssetId: asset.id,
    });
    expect(g.referenceAssetId).toBe(asset.id);
    const body = server.requests.find((r) => r.method === "POST")!.json() as { input: { image: string } };
    expect(body.input.image.startsWith("data:image/jpeg;base64,")).toBe(true);
    const sent = Buffer.from(body.input.image.split(",")[1]!, "base64");
    expect((await sharp(sent).metadata()).format).toBe("jpeg");
  });
});

describe("syncGeneration", () => {
  async function started() {
    const ctx = await setupProject();
    const g = await startGeneration(db, ctx.user.id, { projectId: ctx.project.id, modelId: MODEL, durationSeconds: 5 });
    return { ...ctx, g };
  }

  it("walks queued → processing → succeeded and stores a verified file", async () => {
    const { user, g } = await started();

    expect((await syncGeneration(db, g.id, { force: true }))!.status).toBe("PROCESSING");
    replicate.predictionStatus = { id: "pred_1", status: "processing" };
    expect((await syncGeneration(db, g.id, { force: true }))!.status).toBe("PROCESSING");

    replicate.predictionStatus = succeeded();
    const done = (await syncGeneration(db, g.id, { force: true }))!;
    expect(done.status).toBe("SUCCEEDED");
    expect(done.outputKey).toBe(`videos/${user.id}/${g.id}.mp4`);
    expect(done.outputBytes).toBe(8192);
    expect(done.outputMimeType).toBe("video/mp4");
    expect(done.completedAt).not.toBeNull();

    const stat = await getStorage().stat(done.outputKey!);
    expect(stat?.size).toBe(8192);
    const video = await getDownloadableVideo(db, user.id, g.id);
    expect(video).toMatchObject({ key: done.outputKey, size: 8192, mimeType: "video/mp4" });
  });

  it("FAILS (no video) when the 'video' is actually an HTML page", async () => {
    const { user, g } = await started();
    replicate.predictionStatus = succeeded();
    replicate.downloadResponse = () => ({ body: "<html>Access denied</html>", headers: { "content-type": "text/html" } });

    const done = (await syncGeneration(db, g.id, { force: true }))!;
    expect(done.status).toBe("FAILED");
    expect(done.errorMessage).toMatch(/pas une vidéo valide/);
    expect(done.outputKey).toBeNull();
    await expect(getDownloadableVideo(db, user.id, g.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("FAILS when the downloaded file is empty", async () => {
    const { g } = await started();
    replicate.predictionStatus = succeeded();
    replicate.downloadResponse = () => ({ body: "" });
    const done = (await syncGeneration(db, g.id, { force: true }))!;
    expect(done.status).toBe("FAILED");
    expect(done.outputKey).toBeNull();
  });

  it("propagates a provider-side failure with its message", async () => {
    const { g } = await started();
    replicate.predictionStatus = { id: "pred_1", status: "failed", error: "NSFW content detected" };
    const done = (await syncGeneration(db, g.id, { force: true }))!;
    expect(done.status).toBe("FAILED");
    expect(done.errorMessage).toBe("NSFW content detected");
    expect(done.outputKey).toBeNull();
  });

  it("marks CANCELED when the provider reports a cancellation", async () => {
    const { g } = await started();
    replicate.predictionStatus = { id: "pred_1", status: "canceled" };
    expect((await syncGeneration(db, g.id, { force: true }))!.status).toBe("CANCELED");
  });

  it("retries a transient download error, then succeeds", async () => {
    const { g } = await started();
    replicate.predictionStatus = succeeded();
    let failuresLeft = 1;
    replicate.downloadResponse = () =>
      failuresLeft-- > 0 ? { status: 503, body: "busy" } : { body: fakeMp4(), headers: { "content-type": "video/mp4" } };

    const first = (await syncGeneration(db, g.id, { force: true }))!;
    expect(first.status).toBe("PROCESSING");
    expect(first.downloadAttempts).toBe(1);
    expect(first.errorMessage).toMatch(/Nouvelle tentative/);

    const second = (await syncGeneration(db, g.id, { force: true }))!;
    expect(second.status).toBe("SUCCEEDED");
  });

  it("gives up after 3 failed downloads", async () => {
    const { g } = await started();
    replicate.predictionStatus = succeeded();
    replicate.downloadResponse = () => ({ status: 503, body: "busy" });
    let last = (await syncGeneration(db, g.id, { force: true }))!;
    last = (await syncGeneration(db, g.id, { force: true }))!;
    last = (await syncGeneration(db, g.id, { force: true }))!;
    expect(last.status).toBe("FAILED");
    expect(last.outputKey).toBeNull();
  });

  it("treats an expired output URL (404) as a permanent failure", async () => {
    const { g } = await started();
    replicate.predictionStatus = succeeded();
    replicate.downloadResponse = () => ({ status: 404, body: "gone" });
    const done = (await syncGeneration(db, g.id, { force: true }))!;
    expect(done.status).toBe("FAILED");
    expect(done.errorMessage).toMatch(/plus disponible/);
  });

  it("downloads exactly once when several pollers race", async () => {
    const { g } = await started();
    replicate.predictionStatus = succeeded();
    const results = await Promise.all(Array.from({ length: 6 }, () => syncGeneration(db, g.id, { force: true })));
    expect(results.every((r) => r !== null)).toBe(true);
    expect(replicate.downloads).toBe(1);
    expect((await syncGeneration(db, g.id))!.status).toBe("SUCCEEDED");
  });

  it("throttles provider polling between callers", async () => {
    const { g } = await started();
    await syncGeneration(db, g.id); // first poll claims the slot
    const before = server.requests.filter((r) => r.path === "/predictions/pred_1").length;
    await syncGeneration(db, g.id);
    await syncGeneration(db, g.id);
    expect(server.requests.filter((r) => r.path === "/predictions/pred_1").length).toBe(before);
  });

  it("keeps the job alive and reports the problem when the provider is unreachable", async () => {
    const { g } = await started();
    server.setHandler((req) => (req.path.startsWith("/predictions") ? { status: 503, body: "down" } : { json: {} }));
    const after = (await syncGeneration(db, g.id, { force: true }))!;
    expect(after.status).toBe("PROCESSING");
    expect(after.errorMessage).toMatch(/Suivi temporairement impossible/);
  });

  it("times out stuck jobs after 45 minutes and cancels them at the provider", async () => {
    const { g } = await started();
    const later = new Date(Date.now() + 46 * 60_000);
    const after = (await syncGeneration(db, g.id, { force: true, now: later }))!;
    expect(after.status).toBe("FAILED");
    expect(after.errorMessage).toMatch(/Délai dépassé/);
    expect(server.requests.some((r) => r.path === "/predictions/pred_1/cancel")).toBe(true);
  });

  it("reclaims a download abandoned by a dead worker", async () => {
    const { g } = await started();
    await db.generation.update({ where: { id: g.id }, data: { status: "DOWNLOADING" } });
    replicate.predictionStatus = succeeded();
    const later = new Date(Date.now() + 11 * 60_000);
    const after = (await syncGeneration(db, g.id, { force: true, now: later }))!;
    expect(after.status).toBe("SUCCEEDED");
  });

  it("fails a PENDING job abandoned before reaching the provider", async () => {
    const { user, project } = await setupProject();
    const stale = await db.generation.create({
      data: {
        projectId: project.id, userId: user.id, providerId: "replicate", modelId: MODEL,
        prompt: "p", aspectRatio: "9:16", durationSeconds: 5, status: "PENDING",
      },
    });
    const after = (await syncGeneration(db, stale.id, { now: new Date(Date.now() + 6 * 60_000) }))!;
    expect(after.status).toBe("FAILED");
  });

  it("cron sync advances all in-flight generations", async () => {
    const a = await started();
    const b = await started();
    replicate.predictionStatus = succeeded();
    const res = await syncActiveGenerations(db);
    expect(res.checked).toBe(2);
    expect(res.finished).toBe(2);
    expect((await db.generation.findUnique({ where: { id: a.g.id } }))!.status).toBe("SUCCEEDED");
    expect((await db.generation.findUnique({ where: { id: b.g.id } }))!.status).toBe("SUCCEEDED");
  });
});

describe("ownership & user actions", () => {
  it("does not expose another user's generation or video", async () => {
    const owner = await setupProject();
    const g = await startGeneration(db, owner.user.id, { projectId: owner.project.id, modelId: MODEL, durationSeconds: 5 });
    const intruder = await createUser();
    await expect(getGenerationForUser(db, intruder.id, g.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
    await expect(getDownloadableVideo(db, intruder.id, g.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
    await expect(cancelGeneration(db, intruder.id, g.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("cancels a running job at the provider", async () => {
    const { user, project } = await setupProject();
    const g = await startGeneration(db, user.id, { projectId: project.id, modelId: MODEL, durationSeconds: 5 });
    const after = await cancelGeneration(db, user.id, g.id);
    expect(after.status).toBe("CANCELED");
    expect(server.requests.some((r) => r.method === "POST" && r.path === "/predictions/pred_1/cancel")).toBe(true);
  });

  it("explains when a provider cannot cancel (OpenAI has no cancel endpoint)", async () => {
    useProviders({ OPENAI_API_KEY: "sk-test", OPENAI_BASE_URL: server.url });
    const { user, project } = await setupProject();
    const g = await db.generation.create({
      data: {
        projectId: project.id, userId: user.id, providerId: "openai", modelId: SORA, externalId: "video_1",
        prompt: "p", aspectRatio: "9:16", durationSeconds: 4, status: "PROCESSING",
      },
    });
    await expect(cancelGeneration(db, user.id, g.id)).rejects.toThrow(AppError);
    await expect(cancelGeneration(db, user.id, g.id)).rejects.toThrow(/ne permet pas d'annuler/);
  });
});
