import { Pencil } from "lucide-react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AssetsPanel } from "@/components/project/assets-panel";
import { ConceptPanel, type ConceptView } from "@/components/project/concept-panel";
import { DeleteProjectButton } from "@/components/project/delete-project-button";
import { GenerationPanel } from "@/components/project/generation-panel";
import { ButtonLink } from "@/components/ui/button";
import { Badge, Section } from "@/components/ui/card";
import { getDb } from "@/lib/db";
import { AppError } from "@/lib/errors";
import { requireUser } from "@/lib/session";
import { describeConceptEngine } from "@/modules/concepts";
import { getLatestConcept } from "@/modules/concepts/service";
import { toGenerationDto, toModelOptionView } from "@/modules/generations/dto";
import { getQuotaUsage, listGenerations } from "@/modules/generations/service";
import { ASPECT_RATIO_META, LANGUAGES, TONES, VISUAL_STYLES, type AspectRatio } from "@/modules/projects/presets";
import { getProjectForUser } from "@/modules/projects/service";
import { listModels } from "@/modules/video-providers";

export const metadata: Metadata = { title: "Projet" };

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  const { id } = await params;
  const db = getDb();

  let project;
  try {
    project = await getProjectForUser(db, user.id, id);
  } catch (error) {
    if (error instanceof AppError && error.code === "NOT_FOUND") notFound();
    throw error;
  }

  const [latest, generations, quota] = await Promise.all([
    getLatestConcept(db, user.id, project.id),
    listGenerations(db, user.id, project.id),
    getQuotaUsage(db, user.id),
  ]);

  const conceptView: ConceptView | null = latest
    ? {
        id: latest.concept.id,
        version: latest.concept.version,
        source: latest.concept.source,
        model: latest.concept.model,
        payload: latest.payload,
        videoPrompt: latest.concept.videoPrompt,
        negativePrompt: latest.concept.negativePrompt ?? "",
        edited: latest.concept.videoPromptEdited,
      }
    : null;

  const productImages = project.assets.filter((a) => a.kind === "PRODUCT_IMAGE").map((a) => ({ id: a.id, name: a.originalName }));
  const aspect = project.aspectRatio as AspectRatio;
  const hasActiveJobs = generations.some((g) => g.status === "PENDING" || g.status === "PROCESSING" || g.status === "DOWNLOADING");

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-3">
          <h1 className="text-2xl font-semibold tracking-tight text-balance sm:text-3xl">{project.productName}</h1>
          <div className="flex flex-wrap gap-1.5">
            <Badge>{ASPECT_RATIO_META[aspect]?.label ?? project.aspectRatio}</Badge>
            <Badge>{project.durationSeconds} s visées</Badge>
            <Badge>{TONES.find((t) => t.id === project.tone)?.label ?? project.tone}</Badge>
            <Badge>{VISUAL_STYLES.find((s) => s.id === project.visualStyle)?.label ?? project.visualStyle}</Badge>
            <Badge>{LANGUAGES.find((l) => l.id === project.language)?.label ?? project.language}</Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ButtonLink href={`/projects/${project.id}/edit`} variant="secondary" size="sm">
            <Pencil className="size-4" aria-hidden /> Modifier le brief
          </ButtonLink>
          <DeleteProjectButton projectId={project.id} hasActiveJobs={hasActiveJobs} />
        </div>
      </header>

      <Section step={1} title="Brief" description="Ce que le concept et la vidéo doivent respecter.">
        <dl className="grid gap-5 text-sm sm:grid-cols-2">
          <div className="sm:col-span-2">
            <dt className="mb-1 text-xs font-medium tracking-wide text-subtle uppercase">Description</dt>
            <dd className="leading-relaxed text-muted">{project.description}</dd>
          </div>
          <div>
            <dt className="mb-1 text-xs font-medium tracking-wide text-subtle uppercase">Avantages</dt>
            <dd>
              <ul className="list-inside list-disc space-y-0.5 text-muted">
                {project.benefits.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </dd>
          </div>
          <div>
            <dt className="mb-1 text-xs font-medium tracking-wide text-subtle uppercase">Cible</dt>
            <dd className="text-muted">{project.targetAudience}</dd>
          </div>
          {project.callToAction && (
            <div>
              <dt className="mb-1 text-xs font-medium tracking-wide text-subtle uppercase">Appel à l&apos;action</dt>
              <dd className="text-muted">{project.callToAction}</dd>
            </div>
          )}
          {project.creativeNotes && (
            <div>
              <dt className="mb-1 text-xs font-medium tracking-wide text-subtle uppercase">Notes créatives</dt>
              <dd className="text-muted">{project.creativeNotes}</dd>
            </div>
          )}
        </dl>
      </Section>

      <Section step={2} title="Logo et photos produit" description="Optionnel. Les photos servent à décrire fidèlement le produit et, selon le modèle, d'image de référence.">
        <AssetsPanel
          projectId={project.id}
          assets={project.assets.map((a) => ({ id: a.id, kind: a.kind, originalName: a.originalName, sizeBytes: a.sizeBytes }))}
        />
      </Section>

      <Section step={3} title="Concept publicitaire" description="Idée créative, scènes et prompt vidéo consolidé.">
        <ConceptPanel projectId={project.id} concept={conceptView} engine={describeConceptEngine()} />
      </Section>

      <Section step={4} title="Vidéo" description="Envoi du prompt à un fournisseur de génération vidéo IA, suivi du rendu et téléchargement.">
        <GenerationPanel
          projectId={project.id}
          hasConcept={Boolean(conceptView)}
          aspectRatio={project.aspectRatio}
          targetDuration={project.durationSeconds}
          models={listModels().map(toModelOptionView)}
          productImages={productImages}
          generations={generations.map(toGenerationDto)}
          quota={quota}
        />
      </Section>
    </div>
  );
}
