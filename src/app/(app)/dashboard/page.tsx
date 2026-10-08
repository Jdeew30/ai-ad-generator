import { Clapperboard, Film, ImageIcon, Plus } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { ProviderNotice } from "@/components/app/provider-notice";
import { ButtonLink } from "@/components/ui/button";
import { Badge, Card } from "@/components/ui/card";
import { getDb } from "@/lib/db";
import { requireUser } from "@/lib/session";
import { formatDateTime } from "@/lib/utils";
import { TONES, VISUAL_STYLES } from "@/modules/projects/presets";
import { listProjects } from "@/modules/projects/service";

export const metadata: Metadata = { title: "Projets" };

export default async function DashboardPage() {
  const user = await requireUser();
  const projects = await listProjects(getDb(), user.id);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Mes projets</h1>
          <p className="mt-1 text-sm text-muted">Un projet = un produit, un brief, un concept et ses vidéos.</p>
        </div>
        <ButtonLink href="/projects/new">
          <Plus className="size-4" aria-hidden /> Nouveau projet
        </ButtonLink>
      </header>

      <ProviderNotice />

      {projects.length === 0 ? (
        <Card className="grid place-items-center gap-4 px-6 py-16 text-center">
          <span className="grid size-14 place-items-center rounded-2xl bg-accent-soft text-accent">
            <Clapperboard className="size-7" aria-hidden />
          </span>
          <div>
            <h2 className="text-lg font-semibold">Créez votre première publicité</h2>
            <p className="mx-auto mt-1 max-w-md text-sm text-muted">
              Commencez par un brief produit : nom, description, avantages, cible, ton et format. Le reste se prépare à
              partir de là.
            </p>
          </div>
          <ButtonLink href="/projects/new">
            <Plus className="size-4" aria-hidden /> Nouveau projet
          </ButtonLink>
        </Card>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2">
          {projects.map((p) => {
            const ready = p.generations.length > 0;
            return (
              <li key={p.id}>
                <Link
                  href={`/projects/${p.id}`}
                  className="group block rounded-2xl border border-border bg-surface/80 p-5 transition-colors hover:border-border-strong hover:bg-raised/70"
                >
                  <div className="mb-3 flex items-start justify-between gap-3">
                    <h2 className="line-clamp-1 font-semibold tracking-tight group-hover:text-accent">{p.productName}</h2>
                    {ready && (
                      <Badge tone="success">
                        <Film className="size-3" aria-hidden /> {p.generations[0]?.providerId === "montage" ? "Montage prêt (sans IA)" : "Vidéo prête"}
                      </Badge>
                    )}
                  </div>
                  <p className="mb-4 line-clamp-2 min-h-10 text-sm text-muted">{p.description}</p>
                  <div className="mb-4 flex flex-wrap gap-1.5">
                    <Badge>{p.aspectRatio}</Badge>
                    <Badge>{p.durationSeconds} s</Badge>
                    <Badge>{TONES.find((t) => t.id === p.tone)?.label ?? p.tone}</Badge>
                    <Badge>{VISUAL_STYLES.find((s) => s.id === p.visualStyle)?.label ?? p.visualStyle}</Badge>
                  </div>
                  <div className="flex items-center justify-between text-xs text-subtle">
                    <span className="inline-flex items-center gap-3">
                      <span className="inline-flex items-center gap-1">
                        <ImageIcon className="size-3.5" aria-hidden /> {p._count.assets}
                      </span>
                      <span className="inline-flex items-center gap-1">
                        <Film className="size-3.5" aria-hidden /> {p._count.generations}
                      </span>
                    </span>
                    <span>Modifié {formatDateTime(p.updatedAt)}</span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
