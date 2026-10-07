"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { runAction, type ActionResult } from "@/lib/action";
import { getDb } from "@/lib/db";
import { requireUser } from "@/lib/session";
import { deleteAsset, getAssetForUser } from "./service";

export async function deleteAssetAction(assetId: string): Promise<ActionResult> {
  const user = await requireUser();
  const result = await runAction(async () => {
    const db = getDb();
    const asset = await getAssetForUser(db, user.id, z.string().min(1).max(64).parse(assetId));
    await deleteAsset(db, user.id, asset.id);
    revalidatePath(`/projects/${asset.projectId}`);
    return undefined;
  });
  return result;
}
