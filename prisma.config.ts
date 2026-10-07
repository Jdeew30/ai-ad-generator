import "dotenv/config";
import { defineConfig } from "prisma/config";

// Prisma 7: the connection URL lives here (CLI: migrate/generate/studio), while
// the runtime client receives a driver adapter (see src/lib/db.ts).
export default defineConfig({
  schema: "prisma/schema.prisma",
  migrations: { path: "prisma/migrations" },
  datasource: {
    // `prisma generate` does not connect; a placeholder keeps it usable in CI
    // before DATABASE_URL is provisioned. Migrate commands need the real value.
    url: process.env.DATABASE_URL ?? "postgresql://placeholder:placeholder@localhost:5432/placeholder",
  },
});
