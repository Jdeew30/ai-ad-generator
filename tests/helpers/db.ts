import { randomUUID } from "node:crypto";
import { getDb } from "@/lib/db";

export async function resetDb(): Promise<void> {
  await getDb().$executeRawUnsafe(
    'TRUNCATE "generation","concept","asset","project","session","account","verification","rate_limit","user" RESTART IDENTITY CASCADE',
  );
}

export async function createUser(email = `user-${randomUUID()}@example.com`) {
  return getDb().user.create({ data: { id: randomUUID(), name: "Test User", email } });
}

export async function createProject(
  userId: string,
  overrides: Partial<{ aspectRatio: string; durationSeconds: number }> = {},
) {
  return getDb().project.create({
    data: {
      userId,
      productName: "Gourde Nomad",
      description: "Gourde isotherme en inox qui garde le froid 24h.",
      benefits: ["Garde le froid 24h", "Sans BPA"],
      targetAudience: "Sportifs urbains de 25 à 40 ans",
      tone: "energetic",
      visualStyle: "studio",
      aspectRatio: overrides.aspectRatio ?? "9:16",
      durationSeconds: overrides.durationSeconds ?? 10,
      language: "fr",
    },
  });
}

export async function createConcept(projectId: string, videoPrompt = "A steel water bottle on a wet rock, cinematic.") {
  return getDb().concept.create({
    data: {
      projectId,
      version: 1,
      source: "TEMPLATE",
      payload: {},
      videoPrompt,
    },
  });
}
