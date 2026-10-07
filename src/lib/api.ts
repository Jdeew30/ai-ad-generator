import "server-only";
import { getEnv } from "@/lib/env";
import { AppError } from "@/lib/errors";

export function jsonError(message: string, status: number, headers?: HeadersInit): Response {
  return Response.json({ error: message }, { status, headers });
}

export function statusForError(error: AppError): number {
  switch (error.code) {
    case "NOT_FOUND":
      return 404;
    case "FORBIDDEN":
      return 403;
    case "QUOTA":
      return 429;
    case "NOT_CONFIGURED":
      return 503;
    case "UPSTREAM":
      return 502;
    default:
      return 400;
  }
}

/**
 * CSRF defence for cookie-authenticated, state-changing route handlers (in
 * addition to SameSite=Lax cookies): browsers always send `Origin` on such
 * requests, and it must be our own origin.
 */
export function assertSameOrigin(request: Request): void {
  const origin = request.headers.get("origin");
  const allowed = new URL(getEnv().APP_URL).origin;
  if (!origin || origin !== allowed) {
    throw new AppError("Origine de la requête refusée.", "FORBIDDEN");
  }
}

/** Reads a request body up to `maxBytes`, aborting as soon as the cap is exceeded. */
export async function readBodyCapped(request: Request, maxBytes: number): Promise<Buffer> {
  if (!request.body) return Buffer.alloc(0);
  const reader = request.body.getReader();
  const chunks: Buffer[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel().catch(() => undefined);
      throw new AppError(`Fichier trop volumineux (maximum ${Math.round(maxBytes / 1024 / 1024)} Mo).`);
    }
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks);
}
