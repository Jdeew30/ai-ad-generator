import { unstable_rethrow } from "next/navigation";
import { AppError } from "@/lib/errors";

export type ActionResult<T = undefined> =
  | { ok: true; data: T }
  | { ok: false; error: string; fieldErrors?: Record<string, string> };

/**
 * Wraps a Server Action body: user-safe AppErrors become `{ ok: false }`,
 * Next.js control-flow errors (redirect/notFound) are re-thrown untouched, and
 * anything unexpected is logged and replaced by a generic message.
 */
export async function runAction<T>(fn: () => Promise<T>): Promise<ActionResult<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (error) {
    unstable_rethrow(error);
    if (error instanceof AppError) return { ok: false, error: error.message };
    console.error("[action] unexpected error", error);
    return { ok: false, error: "Une erreur inattendue est survenue. Réessayez." };
  }
}
