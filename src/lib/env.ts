import { z } from "zod";

/** Treat empty strings (as in a copied .env.example) as "not set". */
const optionalString = z.preprocess(
  (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
  z.string().trim().optional(),
);

const optionalUrl = z.preprocess(
  (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
  z.url().optional(),
);

const boolString = (fallback: boolean) =>
  z.preprocess(
    (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
    z
      .enum(["true", "false"])
      .default(fallback ? "true" : "false")
      .transform((v) => v === "true"),
  );

const schema = z.object({
  DATABASE_URL: z.string().min(1, "DATABASE_URL is required"),
  BETTER_AUTH_SECRET: z.string().min(32, "BETTER_AUTH_SECRET must be at least 32 characters (openssl rand -base64 32)"),
  APP_URL: z.preprocess(
    (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
    z.url().default("http://localhost:3000"),
  ),

  ALLOW_SIGNUP: boolString(true),
  DAILY_GENERATION_LIMIT: z.preprocess(
    (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
    z.coerce.number().int().positive().max(1000).default(5),
  ),

  ANTHROPIC_API_KEY: optionalString,
  ANTHROPIC_MODEL: optionalString,
  ANTHROPIC_BASE_URL: optionalUrl,

  OPENAI_API_KEY: optionalString,
  REPLICATE_API_TOKEN: optionalString,
  FAL_KEY: optionalString,

  // Base-URL overrides: for API gateways/proxies, and for pointing the real
  // adapters at a local test server in automated tests.
  OPENAI_BASE_URL: optionalUrl,
  REPLICATE_BASE_URL: optionalUrl,
  FAL_QUEUE_BASE_URL: optionalUrl,

  STORAGE_DIR: z.preprocess(
    (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
    z.string().default("./.data/storage"),
  ),
  CRON_SECRET: z.preprocess(
    (v) => (typeof v === "string" && v.trim() === "" ? undefined : v),
    z.string().min(16, "CRON_SECRET must be at least 16 characters").optional(),
  ),
});

export type Env = z.infer<typeof schema>;

let cached: Env | undefined;

/**
 * Parsed + validated environment, computed lazily so `next build` and tooling
 * that never touch the server runtime do not require every variable.
 */
export function getEnv(): Env {
  if (cached) return cached;
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    const details = parsed.error.issues.map((i) => `  - ${i.path.join(".")}: ${i.message}`).join("\n");
    throw new Error(`Invalid environment configuration:\n${details}`);
  }
  cached = parsed.data;
  return cached;
}

/** Test helper: drop the cache after mutating process.env. */
export function resetEnvCache(): void {
  cached = undefined;
}
