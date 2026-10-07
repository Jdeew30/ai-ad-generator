"use client";

import { Info, Mic, Music, RefreshCw, RotateCcw, Save, Sparkles, Type } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Alert, Badge } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Field, Textarea } from "@/components/ui/form";
import { cn } from "@/lib/utils";
import { generateConceptAction, saveConceptPromptAction } from "@/modules/concepts/actions";
import type { ConceptPayload } from "@/modules/concepts/schema";

export interface ConceptView {
  id: string;
  version: number;
  source: "CLAUDE" | "TEMPLATE";
  model: string | null;
  payload: ConceptPayload | null;
  videoPrompt: string;
  negativePrompt: string;
  edited: boolean;
}

const MAX_PROMPT = 2000;

export function ConceptPanel({
  projectId,
  concept,
  engine,
}: {
  projectId: string;
  concept: ConceptView | null;
  engine: { source: "CLAUDE" | "TEMPLATE"; model: string | null };
}) {
  const router = useRouter();
  const [generating, startGenerating] = useTransition();
  const [saving, startSaving] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Local edit buffer, re-seeded whenever a different concept (or a saved value) arrives.
  const seed = concept ? `${concept.id}:${concept.videoPrompt}:${concept.negativePrompt}` : "";
  const [bufferKey, setBufferKey] = useState(seed);
  const [prompt, setPrompt] = useState(concept?.videoPrompt ?? "");
  const [negative, setNegative] = useState(concept?.negativePrompt ?? "");
  if (bufferKey !== seed) {
    setBufferKey(seed);
    setPrompt(concept?.videoPrompt ?? "");
    setNegative(concept?.negativePrompt ?? "");
  }

  function generate() {
    setError(null);
    startGenerating(async () => {
      const result = await generateConceptAction(projectId);
      if (!result.ok) setError(result.error);
      router.refresh();
    });
  }

  function save() {
    if (!concept) return;
    setError(null);
    setSaved(false);
    startSaving(async () => {
      const result = await saveConceptPromptAction(projectId, concept.id, { videoPrompt: prompt, negativePrompt: negative });
      if (!result.ok) setError(result.error);
      else setSaved(true);
      router.refresh();
    });
  }

  const dirty = concept ? prompt !== concept.videoPrompt || negative !== concept.negativePrompt : false;
  const original = concept?.payload;
  const resetPrompt = original?.videoPrompt;

  return (
    <div className="space-y-5">
      {!concept && (
        <div className="space-y-4">
          <EngineNote engine={engine} />
          <Button onClick={generate} loading={generating} icon={<Sparkles className="size-4" aria-hidden />}>
            {engine.source === "CLAUDE" ? "Générer le concept avec Claude" : "Préparer le prompt (gabarit)"}
          </Button>
        </div>
      )}

      {concept && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {concept.source === "CLAUDE" ? (
                <Badge tone="accent">
                  <Sparkles className="size-3" aria-hidden /> Généré par Claude{concept.model ? ` · ${concept.model}` : ""}
                </Badge>
              ) : (
                <Badge tone="warning">Gabarit, sans IA</Badge>
              )}
              <Badge>Version {concept.version}</Badge>
              {concept.edited && <Badge tone="neutral">Prompt modifié</Badge>}
            </div>
            <Button variant="secondary" size="sm" onClick={generate} loading={generating} icon={<RefreshCw className="size-4" aria-hidden />}>
              Nouvelle version
            </Button>
          </div>

          {concept.source === "TEMPLATE" && (
            <Alert tone="info" title="Ce prompt est assemblé automatiquement à partir de votre brief">
              Aucune rédaction créative n&apos;a eu lieu (pas d&apos;accroche, pas de voix off).{" "}
              {engine.source === "TEMPLATE"
                ? "Définissez ANTHROPIC_API_KEY côté serveur pour obtenir un vrai concept rédigé par Claude."
                : "Une clé Anthropic est maintenant configurée : cliquez sur « Nouvelle version » pour un vrai concept."}
            </Alert>
          )}

          {original && <ConceptBody payload={original} />}

          <div className="space-y-4 rounded-xl border border-border bg-black/20 p-4">
            <div className="flex items-start gap-2">
              <Info className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
              <p className="text-sm text-muted">
                <span className="font-medium text-foreground">Prompt vidéo.</span> C&apos;est exactement ce texte qui sera envoyé au modèle de génération (en anglais, les modèles y répondent mieux). Relisez-le et ajustez-le : c&apos;est le principal levier de qualité.
              </p>
            </div>
            <Field label="Prompt vidéo" htmlFor="videoPrompt" hint={<span className={cn(prompt.length > MAX_PROMPT && "text-danger")}>{prompt.length} / {MAX_PROMPT} caractères</span>}>
              <Textarea id="videoPrompt" value={prompt} onChange={(e) => { setPrompt(e.target.value); setSaved(false); }} rows={6} className="font-mono text-[13px]" />
            </Field>
            <Field label="À éviter (optionnel)" htmlFor="negativePrompt" hint="Défauts visuels à écarter. Pour les modèles sans champ dédié, ajouté au prompt sous la forme « Avoid: … ».">
              <Textarea id="negativePrompt" value={negative} onChange={(e) => { setNegative(e.target.value); setSaved(false); }} rows={2} className="min-h-16 font-mono text-[13px]" />
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={save} loading={saving} disabled={!dirty || prompt.length > MAX_PROMPT} icon={<Save className="size-4" aria-hidden />}>
                Enregistrer
              </Button>
              {resetPrompt && prompt !== resetPrompt && (
                <Button variant="ghost" size="sm" onClick={() => { setPrompt(resetPrompt); setSaved(false); }} icon={<RotateCcw className="size-4" aria-hidden />}>
                  Restaurer l&apos;original
                </Button>
              )}
              {dirty && <span className="text-xs text-warning">Modifications non enregistrées : la génération utilise la version enregistrée.</span>}
              {saved && !dirty && <span className="text-xs text-success">Enregistré.</span>}
            </div>
          </div>
        </>
      )}

      {error && <Alert tone="danger">{error}</Alert>}
    </div>
  );
}

function EngineNote({ engine }: { engine: { source: "CLAUDE" | "TEMPLATE"; model: string | null } }) {
  return engine.source === "CLAUDE" ? (
    <p className="text-sm text-muted">
      Claude ({engine.model}) rédigera l&apos;accroche, les scènes, le script de voix off et un prompt vidéo à partir de votre brief{" "}
      et de vos photos produit.
    </p>
  ) : (
    <Alert tone="warning" title="Pas de clé Anthropic : mode gabarit, sans IA">
      Le prompt vidéo sera simplement assemblé à partir de votre brief — aucune rédaction créative, aucune analyse des
      images. Définissez ANTHROPIC_API_KEY côté serveur pour un vrai concept.
    </Alert>
  );
}

function ConceptBody({ payload }: { payload: ConceptPayload }) {
  const total = payload.scenes.reduce((s, x) => s + x.durationSeconds, 0);
  return (
    <div className="space-y-5">
      {(payload.title || payload.summary || payload.hook) && (
        <div className="space-y-2">
          {payload.title && <h3 className="text-lg font-semibold tracking-tight">{payload.title}</h3>}
          {payload.summary && <p className="text-sm leading-relaxed text-muted">{payload.summary}</p>}
          {payload.hook && (
            <p className="rounded-xl border border-accent/25 bg-accent-soft px-4 py-3 text-sm">
              <span className="font-medium text-accent">Accroche · </span>
              {payload.hook}
            </p>
          )}
        </div>
      )}

      <ol className="space-y-2" aria-label="Scènes">
        {payload.scenes.map((s, i) => (
          <li key={i} className="flex gap-3 rounded-xl border border-border bg-white/3 p-3.5">
            <div className="w-14 shrink-0 text-center">
              <p className="text-xs text-subtle">Scène {i + 1}</p>
              <p className="text-sm font-semibold tabular-nums">{s.durationSeconds} s</p>
            </div>
            <div className="min-w-0 flex-1 space-y-1.5 text-sm">
              <p>{s.visual}</p>
              {s.camera && <p className="text-xs text-subtle">Caméra : {s.camera}</p>}
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                {s.textOverlay && <span className="inline-flex items-center gap-1"><Type className="size-3" aria-hidden /> {s.textOverlay}</span>}
                {s.voiceover && <span className="inline-flex items-center gap-1"><Mic className="size-3" aria-hidden /> {s.voiceover}</span>}
              </div>
            </div>
          </li>
        ))}
        <li className="px-1 text-right text-xs text-subtle">Total : {total} s</li>
      </ol>

      {(payload.voiceoverScript || payload.musicMood || payload.callToAction) && (
        <dl className="grid gap-4 text-sm sm:grid-cols-2">
          {payload.voiceoverScript && (
            <div className="sm:col-span-2">
              <dt className="mb-1 flex items-center gap-1.5 text-xs font-medium tracking-wide text-subtle uppercase"><Mic className="size-3" aria-hidden /> Script voix off</dt>
              <dd className="text-muted">{payload.voiceoverScript}</dd>
            </div>
          )}
          {payload.musicMood && (
            <div>
              <dt className="mb-1 flex items-center gap-1.5 text-xs font-medium tracking-wide text-subtle uppercase"><Music className="size-3" aria-hidden /> Ambiance sonore</dt>
              <dd className="text-muted">{payload.musicMood}</dd>
            </div>
          )}
          {payload.callToAction && (
            <div>
              <dt className="mb-1 text-xs font-medium tracking-wide text-subtle uppercase">Appel à l&apos;action</dt>
              <dd className="text-muted">{payload.callToAction}</dd>
            </div>
          )}
        </dl>
      )}
      <p className="text-xs text-subtle">
        Le texte à l&apos;écran, la voix off et la musique ne sont pas générés par le modèle vidéo : ils sont fournis pour votre montage.
      </p>
    </div>
  );
}
