import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { BriefForm } from "@/components/brief/brief-form";
import { getDb } from "@/lib/db";
import { AppError } from "@/lib/errors";
import { requireUser } from "@/lib/session";
import { LANGUAGE_IDS, TONE_IDS, VISUAL_STYLE_IDS, ASPECT_RATIOS } from "@/modules/projects/presets";
import type { BriefInput } from "@/modules/projects/schema";
import { getProjectForUser } from "@/modules/projects/service";

export const metadata: Metadata = { title: "Modifier le brief" };

function pick<T extends string>(allowed: readonly T[], value: string, fallback: T): T {
  return (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

export default async function EditProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  const { id } = await params;

  let project;
  try {
    project = await getProjectForUser(getDb(), user.id, id);
  } catch (error) {
    if (error instanceof AppError && error.code === "NOT_FOUND") notFound();
    throw error;
  }

  const initial: BriefInput = {
    productName: project.productName,
    description: project.description,
    benefits: project.benefits.length ? project.benefits : [""],
    targetAudience: project.targetAudience,
    tone: pick(TONE_IDS, project.tone, "premium"),
    visualStyle: pick(VISUAL_STYLE_IDS, project.visualStyle, "cinematic"),
    aspectRatio: pick(ASPECT_RATIOS, project.aspectRatio, "9:16"),
    durationSeconds: project.durationSeconds,
    language: pick(LANGUAGE_IDS, project.language, "fr"),
    callToAction: project.callToAction ?? "",
    creativeNotes: project.creativeNotes ?? "",
  };

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header className="space-y-3">
        <Link href={`/projects/${project.id}`} className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground">
          <ArrowLeft className="size-4" aria-hidden /> Retour au projet
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Modifier le brief</h1>
        <p className="text-sm text-muted">
          Les modifications s&apos;appliquent aux prochains concepts. Un concept déjà généré n&apos;est pas réécrit automatiquement.
        </p>
      </header>
      <BriefForm mode="edit" projectId={project.id} initial={initial} />
    </div>
  );
}
