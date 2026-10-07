import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    globals: false,
    // DB-backed tests share one Postgres database; run files serially.
    fileParallelism: false,
    globalSetup: ["tests/global-setup.ts"],
    setupFiles: ["tests/setup.ts"],
    testTimeout: 20_000,
  },
});
