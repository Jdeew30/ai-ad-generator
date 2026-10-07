import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Runs in every test worker before any module under test is imported.
process.env.DATABASE_URL =
  process.env.TEST_DATABASE_URL ?? "postgresql://adstudio:adstudio_dev@localhost:5432/adstudio_test";
process.env.BETTER_AUTH_SECRET = "test-secret-test-secret-test-secret-123456";
process.env.STORAGE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "adstudio-test-"));
process.env.DAILY_GENERATION_LIMIT = "5";
process.env.APP_URL = "http://localhost:3000";
// Never let a developer's real keys leak into tests.
for (const key of ["OPENAI_API_KEY", "REPLICATE_API_TOKEN", "FAL_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"]) {
  delete process.env[key];
}
