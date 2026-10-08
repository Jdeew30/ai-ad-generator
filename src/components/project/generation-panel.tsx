"use client";

import { KeyRound, Play } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { Alert } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { summarizePlan } from "@/modules/montage/plan";
import { startGenerationAction } from "@/modules/generations/actions";
import type { GenerationDto, ModelOptionView } from "@/modules/generations/dto";
import { GenerationCard } from "./generation-card";

interface Props {
  projectId: string;
  hasConcept: boolean;
  aspectRatio: string;
  targetDuration: number;
  models: ModelOptionView[];
  productImages: { id: string; name: string }[];
  generations: GenerationDto[];
  quota: { used: number; limit: number };
  /** What the free montage can use from this project. */
  montageInfo: { photos: number; benefits: number; hasLogo: boolean; hasCta: boolean };
}

function nearest(options: number[], target: number): number {
  return options.reduce((best, d) => (Math.abs(d - target) < Math.abs(best - target) ? d : best), options[0] ?? target);
}

/** Why a model cannot be used for this project, or null if it can. */
function unavailableReason(m: ModelOptionView, aspectRatio: string): string | null {
  if (!m.configured) return `Non configuré — définissez ${m.envVars.join(", ")}`;
  if (!m.aspectRatios.includes(aspectRatio)) return `Format ${aspectRatio} non supporté (${m.aspectRatios.join(", ")})`;
  return null;
}

export function GenerationPanel({ projectId, hasConcept, aspectRatio, targetDuration, models, productImages, generations, quota, montageInfo }: Props) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const usable = useMemo(() => models.filter((m) => !unavailableReason(m, aspectRatio)), [models, aspectRatio]);
  const [modelId, setModelId] = useState<string>(usable[0]?.id ?? "");
  const model = models.find((m) => m.id === modelId);
  const [duration, setDuration] = useState<number>(() => (usable[0] ? nearest(usable[0].durations, targetDuration) : targetDuration));
  const [referenceId, setReferenceId] = useState<string>("");

  const anyAiConfigured = models.some((m) => m.configured && !m.montage);
  const montageReady = models.some((m) => m.configured && m.montage);
  const isMontage = model?.montage === true;
  const quotaLeft = Math.max(0, quota.limit - quota.used);
  // The local montage is free and needs photos, not a concept; AI models need a concept and quota.
  const canStart =
    Boolean(model) &&
    !unavailableReason(model!, aspectRatio) &&
    model!.durations.includes(duration) &&
    (isMontage ? productImages.length > 0 : hasConcept && quotaLeft > 0);

  function pickModel(m: ModelOptionView) {
    setError(null);
    setModelId(m.id);
    setDuration(nearest(m.durations, targetDuration));
    if (!m.imageToVideo) setReferenceId("");
  }

  function start() {
    if (!model) return;
    setError(null);
    startTransition(async () => {
      const result = await startGenerationAction({
        projectId,
        modelId: model.id,
        durationSeconds: duration,
        referenceAssetId: model.imageToVideo && referenceId ? referenceId : null,
      });
      if (!result.ok) setError(result.error);
      else if (result.data.errorMessage) {
        setError(model.montage ? `Le montage a échoué : ${result.data.errorMessage}` : `Le fournisseur a refusé la requête : ${result.data.errorMessage}`);
      }
      router.refresh();
    });
  }

  return (
    <div className="space-y-6">
      {!anyAiConfigured && (
        <Alert tone={montageReady ? "info" : "warning"} title="Aucun fournisseur d'IA vidéo configuré">
          <p>
            {montageReady
              ? "La génération par IA est désactivée : aucune clé API de fournisseur n'est définie côté serveur. Le montage gratuit de vos photos (sans IA) reste disponible ci-dessous."
              : "La génération est désactivée : aucune clé API de fournisseur n'est définie côté serveur, donc aucune vidéo ne peut être produite."}
          </p>
          <p className="mt-1">
            Pour l&apos;IA, définissez au moins une variable parmi {[...new Set(models.filter((m) => !m.montage).flatMap((m) => m.envVars))].join(", ")}, puis redémarrez l&apos;application.{" "}
            <Link href="/settings" className="font-medium text-foreground underline underline-offset-2">Détails</Link>
          </p>
        </Alert>
      )}

      {!hasConcept && !isMontage && <Alert tone="info">Générez d&apos;abord un concept : son prompt vidéo est ce qui sera envoyé au modèle.</Alert>}

      <fieldset className="space-y-2" disabled={pending}>
        <legend className="mb-2 text-sm font-medium">Modèle de génération</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {models.map((m) => {
            const reason = unavailableReason(m, aspectRatio);
            return (
              <label key={m.id} className={cn("block", reason ? "cursor-not-allowed" : "cursor-pointer")}>
                <input type="radio" name="model" className="peer sr-only" value={m.id} checked={modelId === m.id} disabled={Boolean(reason)} onChange={() => pickModel(m)} />
                <span
                  className={cn(
                    "block h-full rounded-xl border p-3.5 transition-colors",
                    reason
                      ? "border-border bg-white/2 opacity-60"
                      : "border-border-strong bg-white/3 hover:border-white/25 peer-checked:border-accent peer-checked:bg-accent-soft peer-focus-visible:outline-2 peer-focus-visible:outline-accent",
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{m.label}</span>
                    {m.montage ? (
                      <span className="rounded-full border border-success/25 bg-success-soft px-2 py-0.5 text-xs font-medium text-success">Gratuit · sans IA</span>
                    ) : (
                      <span className="text-xs text-subtle">{m.providerLabel}</span>
                    )}
                  </span>
                  <span className="mt-1 block text-xs text-muted">{m.description}</span>
                  <span className="mt-2 block text-xs text-subtle">
                    {m.durations.join(" / ")} s · {m.aspectRatios.join(", ")}{m.imageToVideo ? " · image de référence" : ""}
                  </span>
                  {reason && (
                    <span className="mt-2 flex items-start gap-1.5 text-xs text-warning">
                      <KeyRound className="mt-0.5 size-3 shrink-0" aria-hidden /> {reason}
                    </span>
                  )}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      {isMontage && !unavailableReason(model!, aspectRatio) && (
        <Alert tone={productImages.length > 0 ? "info" : "warning"} title="Ce n'est pas de l'IA : un montage de vos propres photos">
          {productImages.length > 0 ? (
            <>
              <p>
                {productImages.length > 1 ? `Vos ${productImages.length} photos produit seront animées` : "Votre photo produit sera animée"} (zoom lent, fondus) avec le nom, les avantages et l&apos;appel à l&apos;action de votre brief, puis exportées en MP4 muet. Aucun prompt n&apos;est utilisé : c&apos;est gratuit et réalisé sur le serveur de l&apos;application. Ne vous attendez pas à de nouvelles images ni à du mouvement généré.
              </p>
              <MontageCoverage duration={duration} info={montageInfo} />
            </>
          ) : (
            "Ajoutez au moins une photo produit dans le bloc « Logo et photos produit » ci-dessus : le montage anime vos propres photos."
          )}
        </Alert>
      )}

      {model && !unavailableReason(model, aspectRatio) && (
        <div className="grid gap-5 sm:grid-cols-2">
          <fieldset className="space-y-2" disabled={pending}>
            <legend className="text-sm font-medium">Durée</legend>
            <div className="flex flex-wrap gap-2">
              {model.durations.map((d) => (
                <label key={d} className="cursor-pointer">
                  <input type="radio" name="duration" className="peer sr-only" checked={duration === d} onChange={() => setDuration(d)} />
                  <span className="block rounded-xl border border-border-strong bg-white/3 px-4 py-2 text-sm transition-colors hover:border-white/25 peer-checked:border-accent peer-checked:bg-accent-soft peer-focus-visible:outline-2 peer-focus-visible:outline-accent">
                    {d} s
                  </span>
                </label>
              ))}
            </div>
            {!model.durations.includes(targetDuration) && (
              <p className="text-xs text-subtle">Votre brief visait {targetDuration} s ; ce modèle ne le propose pas.</p>
            )}
          </fieldset>

          {model.imageToVideo && (
            <fieldset className="space-y-2" disabled={pending}>
              <legend className="text-sm font-medium">Image de référence</legend>
              {productImages.length === 0 ? (
                <p className="text-xs text-subtle">Ajoutez une photo produit pour animer votre visuel (image vers vidéo).</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  <RefOption selected={referenceId === ""} onSelect={() => setReferenceId("")} label="Aucune (texte seul)" />
                  {productImages.map((img) => (
                    <RefOption key={img.id} selected={referenceId === img.id} onSelect={() => setReferenceId(img.id)} label={img.name} src={`/api/assets/${img.id}`} />
                  ))}
                </div>
              )}
            </fieldset>
          )}
        </div>
      )}

      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <Button size="lg" onClick={start} loading={pending} disabled={!canStart} icon={<Play className="size-4" aria-hidden />}>
            {isMontage ? "Créer le montage" : "Générer la vidéo"}
          </Button>
          {!isMontage && (
            <p className="text-xs text-subtle">
              {quotaLeft > 0 ? `${quotaLeft} génération${quotaLeft > 1 ? "s" : ""} restante${quotaLeft > 1 ? "s" : ""} aujourd'hui (limite ${quota.limit}).` : `Limite quotidienne atteinte (${quota.limit}).`}
            </p>
          )}
        </div>
        {model && !unavailableReason(model, aspectRatio) && (
          <p className="text-xs text-subtle">
            {isMontage
              ? "Réalisé sur le serveur de l'application : aucun fournisseur, aucune clé, aucun frais. Le rendu prend de quelques secondes à une minute selon la machine."
              : `Le job sera envoyé à ${model.providerLabel} avec votre clé API et peut être facturé par ce fournisseur selon son tarif.`}
          </p>
        )}
        {error && <Alert tone="danger">{error}</Alert>}
      </div>

      {generations.length > 0 && (
        <div className="space-y-3 border-t border-border pt-6">
          <h3 className="text-sm font-medium">Générations</h3>
          {generations.map((g) => (
            <GenerationCard key={g.id} initial={g} projectId={projectId} />
          ))}
        </div>
      )}
    </div>
  );
}

function RefOption({ selected, onSelect, label, src }: { selected: boolean; onSelect: () => void; label: string; src?: string }) {
  return (
    <label className="cursor-pointer">
      <input type="radio" name="reference" className="peer sr-only" checked={selected} onChange={onSelect} />
      <span className="flex h-full items-center gap-2 rounded-xl border border-border-strong bg-white/3 p-1.5 pr-3 text-xs transition-colors hover:border-white/25 peer-checked:border-accent peer-checked:bg-accent-soft peer-focus-visible:outline-2 peer-focus-visible:outline-accent">
        {src && (
          // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin asset
          <img src={src} alt="" className="size-10 rounded-lg object-cover" />
        )}
        <span className="max-w-32 truncate">{label}</span>
      </span>
    </label>
  );
}

/** Says what the chosen duration will really show, computed from the same plan the renderer uses. */
function MontageCoverage({ duration, info }: { duration: number; info: Props["montageInfo"] }) {
  const plan = summarizePlan({ durationSeconds: duration, photoCount: info.photos, benefitCount: info.benefits, hasLogo: info.hasLogo, hasCallToAction: info.hasCta });
  const partial = plan.photosShown < info.photos || plan.benefitsShown < Math.min(3, info.benefits);
  return (
    <p className="mt-2">
      Avec {duration} s : {plan.scenes} scènes, {plan.photosShown} photo{plan.photosShown > 1 ? "s" : ""} sur {info.photos}, {plan.benefitsShown} avantage{plan.benefitsShown > 1 ? "s" : ""} sur {Math.min(3, info.benefits)}
      {info.hasLogo ? (plan.logoShown ? ", logo sur la dernière scène" : ", logo non affiché") : ""}.{" "}
      {partial ? "Choisissez une durée plus longue pour en montrer davantage. " : ""}
      Les textes trop longs sont raccourcis et les emojis retirés.
    </p>
  );
}
