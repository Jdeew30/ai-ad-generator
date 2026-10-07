import { Readable } from "node:stream";
import { jsonError, statusForError } from "@/lib/api";
import { getDb } from "@/lib/db";
import { AppError } from "@/lib/errors";
import { getSessionUser } from "@/lib/session";
import { getAssetForUser } from "@/modules/assets/service";
import { getStorage } from "@/modules/storage";

export async function GET(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const user = await getSessionUser();
  if (!user) return jsonError("Non authentifié.", 401);

  try {
    const { id } = await ctx.params;
    const asset = await getAssetForUser(getDb(), user.id, id);
    const storage = getStorage();
    const stat = await storage.stat(asset.storageKey);
    if (!stat) return jsonError("Fichier introuvable.", 404);

    const body = Readable.toWeb(storage.createReadStream(asset.storageKey)) as ReadableStream;
    return new Response(body, {
      headers: {
        "Content-Type": asset.mimeType,
        "Content-Length": String(stat.size),
        "Content-Disposition": "inline",
        // Images only, but lock the document down in case a file is ever opened directly.
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, max-age=3600",
      },
    });
  } catch (error) {
    if (error instanceof AppError) return jsonError(error.message, statusForError(error));
    console.error("[assets] serve failed", error);
    return jsonError("Erreur serveur.", 500);
  }
}
