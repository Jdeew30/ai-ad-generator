"use client";

import { Ban, CircleCheck, Download, LoaderCircle, Trash2, TriangleAlert, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { Alert, Badge } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { formatBytes, formatDateTime } from "@/lib/utils";
import { cancelGenerationAction, deleteGenerationAction } from "@/modules/generations/actions";
import type { GenerationDto } from "@/modules/generations/dto";

const ACTIVE = new Set(["PENDING", "PROCESSING", "DOWNLOADING"]);
const POLL_MS = 4000;

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

export function GenerationCard({ initial, projectId }: { initial: GenerationDto; projectId: string }) {
  const router = useRouter();
  const [g, setG] = useState(initial);
  const [now, setNow] = useState(() => Date.now());
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, startBusy] = useTransition();

  // Server-rendered updates (after router.refresh) win over local polling state.
  const [seenInitial, setSeenInitial] = useState(initial);
  if (seenInitial !== initial) {
    setSeenInitial(initial);
    setG(initial);
  }

  const active = ACTIVE.has(g.status);

  // Poll the status endpoint — it also advances the job server-side (no background worker in the MVP).
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      if (stopped) return;
      if (!document.hidden) {
        try {
          const res = await fetch(`/api/generations/${g.id}`, { cache: "no-store" });
          if (res.ok) {
            const next = (await res.json()) as GenerationDto;
            if (stopped) return;
            setG(next);
            if (!ACTIVE.has(next.status)) {
              router.refresh();
              return;
            }
          }
        } catch {
          /* transient network error: keep polling */
        }
      }
      timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, 1500);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [active, g.id, router]);

  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);

  const startedAt = new Date(g.submittedAt ?? g.createdAt).getTime();

  function cancel() {
    setActionError(null);
    startBusy(async () => {
      const r = await cancelGenerationAction(projectId, g.id);
      if (!r.ok) setActionError(r.error);
      router.refresh();
    });
  }

  function remove() {
    setActionError(null);
    startBusy(async () => {
      const r = await deleteGenerationAction(projectId, g.id);
      if (!r.ok) setActionError(r.error);
      setConfirmDelete(false);
      router.refresh();
    });
  }

  return (
    <article className="space-y-4 rounded-xl border border-border bg-white/3 p-4" aria-label={`Génération ${g.modelLabel}`}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={g.status} />
          <span className="text-sm font-medium">{g.modelLabel}</span>
          <span className="text-xs text-subtle">{g.providerLabel} · {g.durationSeconds} s · {g.aspectRatio}</span>
        </div>
        <time className="text-xs text-subtle" dateTime={g.createdAt}>{formatDateTime(new Date(g.createdAt))}</time>
      </header>

      {active && (
        <div className="space-y-2" aria-live="polite">
          <p className="flex items-center gap-2 text-sm text-muted">
            <LoaderCircle className="size-4 animate-spin text-accent" aria-hidden />
            {g.status === "PENDING" && "Envoi du job au fournisseur…"}
            {g.status === "PROCESSING" && `Rendu en cours chez ${g.providerLabel} · ${formatElapsed(now - startedAt)}`}
            {g.status === "DOWNLOADING" && "Rendu terminé, récupération du fichier…"}
          </p>
          {g.progress !== null && g.status === "PROCESSING" && (
            <div className="h-1.5 overflow-hidden rounded-full bg-white/8" role="progressbar" aria-valuenow={g.progress} aria-valuemin={0} aria-valuemax={100}>
              <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${g.progress}%` }} />
            </div>
          )}
          <p className="text-xs text-subtle">
            Les vidéos prennent généralement plusieurs minutes. Vous pouvez quitter cette page : le suivi reprendra à votre retour.
          </p>
          {g.errorMessage && <Alert tone="warning">{g.errorMessage}</Alert>}
        </div>
      )}

      {g.status === "FAILED" && <Alert tone="danger" title="La génération a échoué">{g.errorMessage ?? "Erreur inconnue."}</Alert>}
      {g.status === "CANCELED" && <p className="flex items-center gap-2 text-sm text-muted"><Ban className="size-4" aria-hidden /> Génération annulée.</p>}

      {g.status === "SUCCEEDED" && g.hasVideo && (
        <div className="space-y-3">
          <video
            controls
            preload="metadata"
            playsInline
            src={`/api/generations/${g.id}/video`}
            className="mx-auto max-h-[28rem] w-full rounded-xl bg-black"
            style={{ aspectRatio: g.aspectRatio.replace(":", " / ") }}
          />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <a
              href={`/api/generations/${g.id}/video?download=1`}
              download
              className="inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-medium text-accent-foreground transition-colors hover:bg-accent-strong"
            >
              <Download className="size-4" aria-hidden /> Télécharger{g.outputBytes ? ` (${formatBytes(g.outputBytes)})` : ""}
            </a>
            <span className="inline-flex items-center gap-1.5 text-xs text-success">
              <CircleCheck className="size-3.5" aria-hidden /> Fichier reçu et vérifié
            </span>
          </div>
        </div>
      )}

      <details className="text-xs text-subtle">
        <summary className="cursor-pointer hover:text-muted">Prompt envoyé</summary>
        <p className="mt-2 font-mono leading-relaxed whitespace-pre-wrap text-muted">{g.prompt}</p>
      </details>

      {actionError && <Alert tone="danger">{actionError}</Alert>}

      <div className="flex flex-wrap gap-2">
        {g.canCancel && (
          <Button variant="secondary" size="sm" onClick={cancel} loading={busy} icon={<X className="size-4" aria-hidden />}>
            Annuler la génération
          </Button>
        )}
        {active && !g.canCancel && g.status === "PROCESSING" && (
          <p className="text-xs text-subtle">Ce fournisseur ne permet pas d&apos;annuler un job en cours.</p>
        )}
        {!active && !confirmDelete && (
          <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(true)} icon={<Trash2 className="size-4" aria-hidden />}>
            Supprimer
          </Button>
        )}
        {!active && confirmDelete && (
          <>
            <Button variant="danger" size="sm" onClick={remove} loading={busy}>Confirmer la suppression</Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>Annuler</Button>
          </>
        )}
      </div>
    </article>
  );
}

function StatusBadge({ status }: { status: GenerationDto["status"] }) {
  switch (status) {
    case "SUCCEEDED":
      return <Badge tone="success">Terminée</Badge>;
    case "FAILED":
      return <Badge tone="danger"><TriangleAlert className="size-3" aria-hidden /> Échec</Badge>;
    case "CANCELED":
      return <Badge>Annulée</Badge>;
    case "DOWNLOADING":
      return <Badge tone="accent">Récupération</Badge>;
    case "PROCESSING":
      return <Badge tone="accent">En cours</Badge>;
    default:
      return <Badge tone="accent">Envoi</Badge>;
  }
}
