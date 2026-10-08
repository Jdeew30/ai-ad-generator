import sharp from "sharp";
import { beforeEach, describe, expect, it } from "vitest";
import { getDb } from "@/lib/db";
import { addAsset, deleteAsset, getAssetForUser } from "@/modules/assets/service";
import { briefSchema } from "@/modules/projects/schema";
import { createProject, deleteProject, getProjectForUser, listProjects, updateProject } from "@/modules/projects/service";
import { getStorage } from "@/modules/storage";
import { createConcept, createProject as seedProject, createUser, resetDb } from "./helpers/db";

const db = getDb();

async function png(color = "#336699") {
  return sharp({ create: { width: 40, height: 40, channels: 3, background: color } }).png().toBuffer();
}

beforeEach(resetDb);

describe("addAsset", () => {
  it("stores a valid image under a server-generated key and records its real type", async () => {
    const user = await createUser();
    const project = await seedProject(user.id);
    const asset = await addAsset(db, user.id, {
      projectId: project.id,
      kind: "PRODUCT_IMAGE",
      originalName: "../../../etc/cron.d/evil.png",
      bytes: await png(),
    });
    expect(asset.mimeType).toBe("image/png");
    expect(asset.originalName).toBe("evil.png");
    expect(asset.storageKey).toMatch(new RegExp(`^assets/${project.id}/[0-9a-f-]{36}\\.png$`));
    expect(await getStorage().stat(asset.storageKey)).not.toBeNull();
  });

  it("ignores a spoofed extension/type: content decides", async () => {
    const user = await createUser();
    const project = await seedProject(user.id);
    const jpeg = await sharp({ create: { width: 10, height: 10, channels: 3, background: "#fff" } }).jpeg().toBuffer();
    const asset = await addAsset(db, user.id, { projectId: project.id, kind: "LOGO", originalName: "logo.png", bytes: jpeg });
    expect(asset.mimeType).toBe("image/jpeg");
    expect(asset.storageKey.endsWith(".jpg")).toBe(true);
  });

  it("rejects SVG, HTML disguised as PNG, empty and oversized files — storing nothing", async () => {
    const user = await createUser();
    const project = await seedProject(user.id);
    const attempt = (bytes: Buffer, name = "x.png") =>
      addAsset(db, user.id, { projectId: project.id, kind: "PRODUCT_IMAGE", originalName: name, bytes });

    await expect(attempt(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'), "x.svg")).rejects.toThrow(/Format non supporté/);
    await expect(attempt(Buffer.from("<html>not an image</html>"))).rejects.toThrow(/Format non supporté/);
    await expect(attempt(Buffer.alloc(0))).rejects.toThrow(/vide/);
    await expect(attempt(Buffer.alloc(8 * 1024 * 1024 + 1, 1))).rejects.toThrow(/volumineux/);
    expect(await db.asset.count()).toBe(0);
  });

  it("refuses images with an abusive pixel count even when the file is tiny (decompression-bomb style)", async () => {
    const user = await createUser();
    const project = await seedProject(user.id);
    // 6000 x 6000 = 36 Mpx of flat colour compresses to a few KB.
    const huge = await sharp({ create: { width: 6000, height: 6000, channels: 3, background: "#808080" } }).png({ compressionLevel: 9 }).toBuffer();
    expect(huge.length).toBeLessThan(8 * 1024 * 1024);
    await expect(addAsset(db, user.id, { projectId: project.id, kind: "PRODUCT_IMAGE", originalName: "bomb.png", bytes: huge })).rejects.toThrow(/mégapixels/);
    expect(await db.asset.count()).toBe(0);

    const ok = await sharp({ create: { width: 4000, height: 3000, channels: 3, background: "#808080" } }).jpeg().toBuffer(); // 12 Mpx
    await expect(addAsset(db, user.id, { projectId: project.id, kind: "PRODUCT_IMAGE", originalName: "ok.jpg", bytes: ok })).resolves.toBeTruthy();
  }, 60_000);

  it("rejects a file that has valid magic bytes but cannot be decoded", async () => {
    const user = await createUser();
    const project = await seedProject(user.id);
    const fake = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64, 7)]);
    await expect(addAsset(db, user.id, { projectId: project.id, kind: "LOGO", originalName: "x.png", bytes: fake })).rejects.toThrow(/illisible/);
  });

  it("enforces per-kind limits", async () => {
    const user = await createUser();
    const project = await seedProject(user.id);
    await addAsset(db, user.id, { projectId: project.id, kind: "LOGO", originalName: "a.png", bytes: await png() });
    await expect(
      addAsset(db, user.id, { projectId: project.id, kind: "LOGO", originalName: "b.png", bytes: await png("#111") }),
    ).rejects.toThrow(/Un seul logo/);

    for (let i = 0; i < 5; i++) {
      await addAsset(db, user.id, { projectId: project.id, kind: "PRODUCT_IMAGE", originalName: `p${i}.png`, bytes: await png(`#00${i}000`) });
    }
    await expect(
      addAsset(db, user.id, { projectId: project.id, kind: "PRODUCT_IMAGE", originalName: "p6.png", bytes: await png("#abc") }),
    ).rejects.toThrow(/5 images/);
  });

  it("cannot upload into, read, or delete another user's project/asset", async () => {
    const owner = await createUser();
    const intruder = await createUser();
    const project = await seedProject(owner.id);
    await expect(
      addAsset(db, intruder.id, { projectId: project.id, kind: "LOGO", originalName: "x.png", bytes: await png() }),
    ).rejects.toMatchObject({ code: "NOT_FOUND" });

    const asset = await addAsset(db, owner.id, { projectId: project.id, kind: "LOGO", originalName: "x.png", bytes: await png() });
    await expect(getAssetForUser(db, intruder.id, asset.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
    await expect(deleteAsset(db, intruder.id, asset.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect(await getStorage().stat(asset.storageKey)).not.toBeNull();
  });

  it("deleting an asset removes the row and the file", async () => {
    const user = await createUser();
    const project = await seedProject(user.id);
    const asset = await addAsset(db, user.id, { projectId: project.id, kind: "LOGO", originalName: "x.png", bytes: await png() });
    await deleteAsset(db, user.id, asset.id);
    expect(await db.asset.count()).toBe(0);
    expect(await getStorage().stat(asset.storageKey)).toBeNull();
  });
});

describe("projects service", () => {
  const brief = briefSchema.parse({
    productName: "Casque Aura",
    description: "Casque audio à réduction de bruit active.",
    benefits: ["ANC", "30 h d'autonomie"],
    targetAudience: "Télétravailleurs",
    tone: "premium",
    visualStyle: "cinematic",
    aspectRatio: "16:9",
    durationSeconds: 8,
    language: "fr",
    callToAction: "",
    creativeNotes: "",
  });

  it("creates, lists and fetches only the owner's projects", async () => {
    const a = await createUser();
    const b = await createUser();
    const pa = await createProject(db, a.id, brief);
    await createProject(db, b.id, brief);

    expect((await listProjects(db, a.id)).map((p) => p.id)).toEqual([pa.id]);
    await expect(getProjectForUser(db, b.id, pa.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
    expect((await getProjectForUser(db, a.id, pa.id)).productName).toBe("Casque Aura");
  });

  it("updates only the owner's project", async () => {
    const a = await createUser();
    const b = await createUser();
    const p = await createProject(db, a.id, brief);
    await expect(updateProject(db, b.id, p.id, { ...brief, productName: "Hacked" })).rejects.toMatchObject({ code: "NOT_FOUND" });
    await updateProject(db, a.id, p.id, { ...brief, productName: "Casque Aura 2" });
    expect((await getProjectForUser(db, a.id, p.id)).productName).toBe("Casque Aura 2");
  });

  it("deleting a project cascades rows and removes every stored file", async () => {
    const user = await createUser();
    const p = await createProject(db, user.id, brief);
    const asset = await addAsset(db, user.id, { projectId: p.id, kind: "LOGO", originalName: "l.png", bytes: await png() });
    await createConcept(p.id);
    const videoKey = `videos/${user.id}/g1.mp4`;
    await getStorage().put(videoKey, Buffer.alloc(2048), { maxBytes: 10_000 });
    await db.generation.create({
      data: {
        projectId: p.id, userId: user.id, providerId: "replicate", modelId: "replicate:bytedance/seedance-1-lite",
        prompt: "p", aspectRatio: "16:9", durationSeconds: 5, status: "SUCCEEDED", outputKey: videoKey, outputBytes: 2048, outputMimeType: "video/mp4",
      },
    });

    await expect(deleteProject(db, (await createUser()).id, p.id)).rejects.toMatchObject({ code: "NOT_FOUND" });
    await deleteProject(db, user.id, p.id);

    expect(await db.project.count()).toBe(0);
    expect(await db.asset.count()).toBe(0);
    expect(await db.concept.count()).toBe(0);
    expect(await db.generation.count()).toBe(0);
    expect(await getStorage().stat(asset.storageKey)).toBeNull();
    expect(await getStorage().stat(videoKey)).toBeNull();
  });
});
