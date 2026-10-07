import type { Readable } from "node:stream";

export interface ByteRange {
  /** Inclusive start offset. */
  start: number;
  /** Inclusive end offset. */
  end: number;
}

/**
 * Minimal object-storage contract. The MVP ships a local-disk driver; an S3/R2
 * driver can implement the same interface for serverless deployments
 * (see docs/ARCHITECTURE.md §8 — not shipped yet).
 */
export interface StorageDriver {
  /**
   * Stores a stream/buffer under `key`. Must be atomic (a reader never sees a
   * partial object) and must fail — leaving nothing behind — if more than
   * `maxBytes` are written.
   */
  put(key: string, data: Buffer | Readable, options: { maxBytes: number }): Promise<{ size: number }>;
  /** Returns null when the object does not exist. */
  stat(key: string): Promise<{ size: number } | null>;
  createReadStream(key: string, range?: ByteRange): Readable;
  /** No-op if the object does not exist. */
  delete(key: string): Promise<void>;
}

export class StorageLimitError extends Error {
  readonly __brand = "StorageLimitError" as const;

  constructor(readonly maxBytes: number) {
    super(`Object exceeds the maximum allowed size of ${maxBytes} bytes`);
    this.name = "StorageLimitError";
  }
}

/** Brand-based check (see AppError): robust to Next.js bundling a module twice. */
export function isStorageLimitError(error: unknown): error is StorageLimitError {
  return (
    error instanceof StorageLimitError ||
    (typeof error === "object" && error !== null && (error as { __brand?: unknown }).__brand === "StorageLimitError")
  );
}
