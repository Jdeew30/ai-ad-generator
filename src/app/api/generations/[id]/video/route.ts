import { Readable } from "node:stream";
import { jsonError, statusForError } from "@/lib/api";
import { getDb } from "@/lib/db";
import { isAppError } from "@/lib/errors";
import { parseRange } from "@/lib/http-range";
import { getSessionUser } from "@/lib/session";
import { getDownloadableVideo } from "@/modules/generations/service";
import { videoExtension } from "@/modules/generations/video-file";
import { getStorage } from "@/modules/storage";

function slug(text: string): string {
  return (
    text
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "video"
  );
}

/**
 * Streams the stored video. Exists only for generations that really produced a
 * verified file (see getDownloadableVideo). Supports Range requests so the
 * browser <video> element can seek. `?download=1` forces a file download.
 */
export async function GET(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const user = await getSessionUser();
  if (!user) return jsonError("Non authentifié.", 401);

  try {
    const { id } = await ctx.params;
    const db = getDb();
    const video = await getDownloadableVideo(db, user.id, id);
    const project = await db.project.findUnique({ where: { id: video.generation.projectId }, select: { productName: true } });

    const headers: Record<string, string> = {
      "Content-Type": video.mimeType,
      "Accept-Ranges": "bytes",
      "Cache-Control": "private, max-age=3600",
    };
    if (new URL(request.url).searchParams.get("download") === "1") {
      const ext = videoExtension(video.mimeType as "video/mp4" | "video/webm" | "video/quicktime");
      headers["Content-Disposition"] = `attachment; filename="${slug(project?.productName ?? "video")}-${id.slice(-6)}.${ext}"`;
    }

    const range = parseRange(request.headers.get("range"), video.size);
    if (range === "unsatisfiable") {
      return new Response(null, { status: 416, headers: { ...headers, "Content-Range": `bytes */${video.size}` } });
    }

    const storage = getStorage();
    if (range) {
      const stream = Readable.toWeb(storage.createReadStream(video.key, range)) as ReadableStream;
      return new Response(stream, {
        status: 206,
        headers: {
          ...headers,
          "Content-Range": `bytes ${range.start}-${range.end}/${video.size}`,
          "Content-Length": String(range.end - range.start + 1),
        },
      });
    }
    const stream = Readable.toWeb(storage.createReadStream(video.key)) as ReadableStream;
    return new Response(stream, { headers: { ...headers, "Content-Length": String(video.size) } });
  } catch (error) {
    if (isAppError(error)) return jsonError(error.message, statusForError(error));
    console.error("[generations] video failed", error);
    return jsonError("Erreur serveur.", 500);
  }
}
