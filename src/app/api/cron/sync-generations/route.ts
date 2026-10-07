import { timingSafeEqual } from "node:crypto";
import { jsonError } from "@/lib/api";
import { getDb } from "@/lib/db";
import { getEnv } from "@/lib/env";
import { syncActiveGenerations } from "@/modules/generations/service";

function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  return ab.length === bb.length && timingSafeEqual(ab, bb);
}

/**
 * Advances every in-flight generation, so jobs finish (and files are copied
 * before the provider's URLs expire) even if nobody has the page open.
 * Call it from a cron with `Authorization: Bearer $CRON_SECRET`.
 */
export async function GET(request: Request) {
  const secret = getEnv().CRON_SECRET;
  if (!secret) return jsonError("CRON_SECRET n'est pas configuré.", 503);

  const header = request.headers.get("authorization") ?? "";
  if (!header.startsWith("Bearer ") || !safeEqual(header.slice(7), secret)) {
    return jsonError("Non autorisé.", 401);
  }

  const result = await syncActiveGenerations(getDb());
  return Response.json(result, { headers: { "Cache-Control": "no-store" } });
}
