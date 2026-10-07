import { betterAuth } from "better-auth";
import { prismaAdapter } from "better-auth/adapters/prisma";
import { nextCookies } from "better-auth/next-js";
import { getDb } from "@/lib/db";
import { getEnv } from "@/lib/env";

function createAuth() {
  const env = getEnv();
  const isProd = process.env.NODE_ENV === "production";

  return betterAuth({
    appName: "AI Ad Studio",
    baseURL: env.APP_URL,
    secret: env.BETTER_AUTH_SECRET,
    trustedOrigins: [env.APP_URL],
    database: prismaAdapter(getDb(), { provider: "postgresql" }),
    emailAndPassword: {
      enabled: true,
      autoSignIn: true,
      disableSignUp: !env.ALLOW_SIGNUP,
      minPasswordLength: 10,
      maxPasswordLength: 128,
      // No e-mail service is wired in the MVP, so verification / reset flows are
      // intentionally not enabled (see docs/ARCHITECTURE.md §10).
      requireEmailVerification: false,
    },
    session: {
      expiresIn: 60 * 60 * 24 * 14,
      updateAge: 60 * 60 * 24,
    },
    rateLimit: {
      enabled: true,
      storage: "database",
      window: 60,
      max: 100,
      customRules: {
        "/sign-in/email": { window: 60, max: 5 },
        "/sign-up/email": { window: 60, max: 5 },
      },
    },
    advanced: { useSecureCookies: isProd },
    // Must stay last: lets server actions set the session cookie.
    plugins: [nextCookies()],
  });
}

type AuthInstance = ReturnType<typeof createAuth>;

const globalForAuth = globalThis as unknown as { __auth?: AuthInstance };

export function getAuth(): AuthInstance {
  if (!globalForAuth.__auth) {
    globalForAuth.__auth = createAuth();
  }
  return globalForAuth.__auth;
}
