import { getEnv } from "@/lib/env";
import { LocalStorageDriver } from "./local";
import type { StorageDriver } from "./types";

export type { StorageDriver, ByteRange } from "./types";
export { StorageLimitError, isStorageLimitError } from "./types";

const globalForStorage = globalThis as unknown as { __storage?: StorageDriver };

/** The configured storage driver (local disk in the MVP). */
export function getStorage(): StorageDriver {
  if (!globalForStorage.__storage) {
    globalForStorage.__storage = new LocalStorageDriver(getEnv().STORAGE_DIR);
  }
  return globalForStorage.__storage;
}

/** Test seam: inject a driver. */
export function setStorageForTests(driver: StorageDriver | undefined): void {
  globalForStorage.__storage = driver;
}
