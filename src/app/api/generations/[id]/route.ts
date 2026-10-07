import { jsonError, statusForError } from "@/lib/api";
import { getDb } from "@/lib/db";
import { AppError } from "@/lib/errors";
import { getSessionUser } from "@/lib/session";
import { toGenerationDto } from "@/modules/generations/dto";
import { getGenerationForUser, syncGeneration } from "@/modules/generations/service";

/**
 * Status endpoint polled by the UI. Each call also advances the job (poll the
 * provider, download the file when ready) — there is no background worker in the
 * MVP. The sync is idempotent and throttled per generation (see service.ts).
 */
export async function GET(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const user = await getSessionUser();
  if (!user) return jsonError("Non authentifié.", 401);

  try {
    const { id } = await ctx.params;
    const db = getDb();
    await getGenerationForUser(db, user.id, id); // ownership check before any provider call
    const g = await syncGeneration(db, id);
    if (!g) return jsonError("Génération introuvable.", 404);
    return Response.json(toGenerationDto(g), { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (error instanceof AppError) return jsonError(error.message, statusForError(error));
    console.error("[generations] status failed", error);
    return jsonError("Erreur serveur.", 500);
  }
}
