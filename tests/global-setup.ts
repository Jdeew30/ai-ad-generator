import { execSync } from "node:child_process";

/** Applies migrations to the dedicated test database once per run. */
export default function setup() {
  const url =
    process.env.TEST_DATABASE_URL ?? "postgresql://adstudio:adstudio_dev@localhost:5432/adstudio_test";
  execSync("npx prisma migrate deploy", {
    env: { ...process.env, DATABASE_URL: url },
    stdio: "pipe",
  });
}
