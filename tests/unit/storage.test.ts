import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { LocalStorageDriver } from "@/modules/storage/local";
import { StorageLimitError } from "@/modules/storage/types";

let root: string;
let storage: LocalStorageDriver;

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "adstudio-storage-"));
  storage = new LocalStorageDriver(root);
});
afterEach(() => fs.rmSync(root, { recursive: true, force: true }));

async function readAll(stream: Readable): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const c of stream) chunks.push(c as Buffer);
  return Buffer.concat(chunks);
}

describe("LocalStorageDriver", () => {
  it("stores, stats, reads (with ranges) and deletes", async () => {
    const data = Buffer.from("0123456789");
    expect(await storage.put("videos/u1/a.mp4", data, { maxBytes: 100 })).toEqual({ size: 10 });
    expect(await storage.stat("videos/u1/a.mp4")).toEqual({ size: 10 });
    expect((await readAll(storage.createReadStream("videos/u1/a.mp4"))).toString()).toBe("0123456789");
    expect((await readAll(storage.createReadStream("videos/u1/a.mp4", { start: 2, end: 4 }))).toString()).toBe("234");
    await storage.delete("videos/u1/a.mp4");
    expect(await storage.stat("videos/u1/a.mp4")).toBeNull();
    await expect(storage.delete("videos/u1/a.mp4")).resolves.toBeUndefined(); // idempotent
  });

  it("refuses keys that could escape the storage root", () => {
    for (const key of ["../outside", "a/../../outside", "/etc/passwd", "a//b", "a/./b", "", "a b", "a\\b", "..", "a/..", "con\u0000"]) {
      expect(() => storage.resolve(key), JSON.stringify(key)).toThrow(/Invalid storage key/);
    }
    expect(storage.resolve("assets/p1/file.png").startsWith(root)).toBe(true);
  });

  it("enforces the size limit on streams and leaves nothing behind", async () => {
    const big = Readable.from([Buffer.alloc(60), Buffer.alloc(60)]);
    await expect(storage.put("videos/u1/big.mp4", big, { maxBytes: 100 })).rejects.toBeInstanceOf(StorageLimitError);
    expect(await storage.stat("videos/u1/big.mp4")).toBeNull();
    const leftovers = fs.readdirSync(path.join(root, "videos/u1"));
    expect(leftovers).toEqual([]); // no .part files
  });

  it("is atomic: an interrupted write never becomes visible", async () => {
    const broken = new Readable({
      read() {
        this.push(Buffer.from("partial"));
        this.destroy(new Error("network dropped"));
      },
    });
    await expect(storage.put("videos/u1/cut.mp4", broken, { maxBytes: 100 })).rejects.toThrow("network dropped");
    expect(await storage.stat("videos/u1/cut.mp4")).toBeNull();
    expect(fs.readdirSync(path.join(root, "videos/u1"))).toEqual([]);
  });

  it("overwrites an existing key atomically", async () => {
    await storage.put("a/b.bin", Buffer.from("one"), { maxBytes: 10 });
    await storage.put("a/b.bin", Buffer.from("three"), { maxBytes: 10 });
    expect((await readAll(storage.createReadStream("a/b.bin"))).toString()).toBe("three");
  });
});
