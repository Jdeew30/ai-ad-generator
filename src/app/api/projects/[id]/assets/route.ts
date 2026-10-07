import { assertSameOrigin, jsonError, readBodyCapped, statusForError } from "@/lib/api";
import { getDb } from "@/lib/db";
import { AppError } from "@/lib/errors";
import { getSessionUser } from "@/lib/session";
import { addAsset } from "@/modules/assets/service";
import { MAX_ASSET_BYTES, sanitizeDisplayName } from "@/modules/assets/image";

/**
 * Raw-body upload (not multipart) so the size cap is enforced while streaming.
 * POST /api/projects/:id/assets?kind=LOGO|PRODUCT_IMAGE&name=photo.png
 * The declared Content-Type and file name are never trusted (see assets/image.ts).
 */
export async function POST(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const user = await getSessionUser();
  if (!user) return jsonError("Non authentifié.", 401);

  try {
    assertSameOrigin(request);
    const { id } = await ctx.params;
    const url = new URL(request.url);
    const kind = url.searchParams.get("kind");
    if (kind !== "LOGO" && kind !== "PRODUCT_IMAGE") throw new AppError("Type de fichier invalide.");

    const declared = Number(request.headers.get("content-length") ?? 0);
    if (declared > MAX_ASSET_BYTES) throw new AppError("Fichier trop volumineux (maximum 8 Mo).");

    const bytes = await readBodyCapped(request, MAX_ASSET_BYTES);
    const asset = await addAsset(getDb(), user.id, {
      projectId: id,
      kind,
      originalName: sanitizeDisplayName(url.searchParams.get("name") ?? "image"),
      bytes,
    });
    return Response.json({ id: asset.id }, { status: 201 });
  } catch (error) {
    if (error instanceof AppError) return jsonError(error.message, statusForError(error));
    console.error("[assets] upload failed", error);
    return jsonError("L'envoi du fichier a échoué.", 500);
  }
}
