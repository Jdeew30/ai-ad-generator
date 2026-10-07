import { randomUUID } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import path from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { StorageLimitError, type ByteRange, type StorageDriver } from "./types";

/** Keys are server-generated; this is a defence-in-depth check, not the primary control. */
const KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_\-./]*$/;

export class LocalStorageDriver implements StorageDriver {
  private readonly root: string;

  constructor(rootDir: string) {
    this.root = path.resolve(rootDir);
  }

  /** Maps a key to an absolute path, refusing anything that escapes the root. */
  resolve(key: string): string {
    if (!KEY_PATTERN.test(key) || key.split("/").some((seg) => seg === ".." || seg === "." || seg === "")) {
      throw new Error(`Invalid storage key: ${JSON.stringify(key)}`);
    }
    const full = path.resolve(this.root, key);
    if (!full.startsWith(this.root + path.sep)) {
      throw new Error(`Invalid storage key: ${JSON.stringify(key)}`);
    }
    return full;
  }

  async put(key: string, data: Buffer | Readable, options: { maxBytes: number }): Promise<{ size: number }> {
    const target = this.resolve(key);
    await mkdir(path.dirname(target), { recursive: true });
    const tmp = `${target}.${randomUUID()}.part`;

    let size = 0;
    const limiter = new Transform({
      transform(chunk: Buffer, _enc, cb) {
        size += chunk.length;
        if (size > options.maxBytes) {
          cb(new StorageLimitError(options.maxBytes));
          return;
        }
        cb(null, chunk);
      },
    });

    try {
      const source = Buffer.isBuffer(data) ? Readable.from([data]) : data;
      await pipeline(source, limiter, createWriteStream(tmp, { flags: "wx" }));
      await rename(tmp, target);
      return { size };
    } catch (error) {
      await rm(tmp, { force: true });
      throw error;
    }
  }

  async stat(key: string): Promise<{ size: number } | null> {
    try {
      const s = await stat(this.resolve(key));
      return s.isFile() ? { size: s.size } : null;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
      throw error;
    }
  }

  createReadStream(key: string, range?: ByteRange): Readable {
    return createReadStream(this.resolve(key), range ? { start: range.start, end: range.end } : undefined);
  }

  async delete(key: string): Promise<void> {
    await rm(this.resolve(key), { force: true });
  }
}
