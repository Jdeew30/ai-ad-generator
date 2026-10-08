"use client";

import { ArrowLeft, ArrowRight, Check, Plus, Sparkles, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { cn } from "@/lib/utils";
import { createProjectAction, updateProjectAction } from "@/modules/projects/actions";
import {
  ASPECT_RATIOS,
  ASPECT_RATIO_META,
  DURATIONS,
  LANGUAGES,
  TONES,
  VISUAL_STYLES,
  type AspectRatio,
} from "@/modules/projects/presets";
import { briefSchema, DEFAULT_BRIEF, type BriefInput } from "@/modules/projects/schema";
import { ChoiceGroup } from "./choice-group";
import { MediaPicker, MAX_PRODUCT_IMAGES, validateFile, type PendingFile } from "./media-picker";

type Props = { mode: "create" } | { mode: "edit"; projectId: string; initial: BriefInput };

const STEPS = [
  { title: "Produit", fields: ["productName", "description", "benefits"] },
  { title: "Message & style", fields: ["targetAudience", "tone", "visualStyle", "language", "callToAction", "creativeNotes"] },
  { title: "Format & médias", fields: ["aspectRatio", "durationSeconds"] },
] as const;

function normalize(values: BriefInput): BriefInput {
  return { ...values, benefits: values.benefits.map((b) => b.trim()).filter(Boolean) };
}

function AspectShape({ ratio }: { ratio: AspectRatio }) {
  const [w, h] = ratio.split(":").map(Number) as [number, number];
  return (
    <span className="grid h-10 place-items-center">
      <span className="block rounded-[5px] border-2 border-current opacity-80" style={{ aspectRatio: `${w} / ${h}`, height: ratio === "16:9" ? 22 : 38 }} />
    </span>
  );
}

export function BriefForm(props: Props) {
  const router = useRouter();
  const editing = props.mode === "edit";
  const [values, setValues] = useState<BriefInput>(editing ? props.initial : DEFAULT_BRIEF);
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [fileErrors, setFileErrors] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [uploadReport, setUploadReport] = useState<{ projectId: string; failures: string[] } | null>(null);

  const set = <K extends keyof BriefInput>(key: K, value: BriefInput[K]) => {
    setValues((v) => ({ ...v, [key]: value }));
    setErrors((e) => (e[key] ? { ...e, [key]: "" } : e));
  };

  /** Validates the given fields; returns true when they are all valid. */
  function validate(fields: readonly string[]): boolean {
    const parsed = briefSchema.safeParse(normalize(values));
    const next: Record<string, string> = {};
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const key = String(issue.path[0]);
        if (fields.includes(key)) next[key] ??= issue.message;
      }
    }
    setErrors((prev) => ({ ...prev, ...Object.fromEntries(fields.map((f) => [f, next[f] ?? ""])) }));
    return Object.keys(next).length === 0;
  }

  function addFiles(list: File[], kind: PendingFile["kind"]) {
    const problems: string[] = [];
    const accepted: PendingFile[] = [];
    const room = kind === "LOGO" ? 1 : MAX_PRODUCT_IMAGES - files.filter((f) => f.kind === "PRODUCT_IMAGE").length;
    for (const file of list) {
      const problem = validateFile(file);
      if (problem) problems.push(problem);
      else if (accepted.length < room) accepted.push({ id: crypto.randomUUID(), file, kind, previewUrl: URL.createObjectURL(file) });
      else problems.push(kind === "LOGO" ? "Un seul logo est accepté." : `${MAX_PRODUCT_IMAGES} photos maximum.`);
    }
    setFileErrors(problems);
    if (accepted.length) {
      setFiles((prev) => [...(kind === "LOGO" ? prev.filter((f) => f.kind !== "LOGO") : prev), ...accepted]);
    }
  }

  function removeFile(id: string) {
    setFiles((prev) => {
      const target = prev.find((f) => f.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((f) => f.id !== id);
    });
  }

  function next() {
    if (validate(STEPS[step]!.fields)) setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  async function uploadAll(projectId: string): Promise<string[]> {
    const failures: string[] = [];
    for (const f of files) {
      try {
        const res = await fetch(
          `/api/projects/${projectId}/assets?kind=${f.kind}&name=${encodeURIComponent(f.file.name)}`,
          { method: "POST", body: f.file, headers: { "Content-Type": f.file.type } },
        );
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { error?: string };
          failures.push(`${f.file.name} : ${body.error ?? "échec de l'envoi"}`);
        }
      } catch {
        failures.push(`${f.file.name} : échec de l'envoi`);
      }
    }
    return failures;
  }

  async function submit() {
    setFormError(null);
    const all = STEPS.flatMap((s) => s.fields);
    if (!validate(all)) {
      const firstBad = STEPS.findIndex((s) => s.fields.some((f) => briefSchema.safeParse(normalize(values)).error?.issues.some((i) => i.path[0] === f)));
      if (firstBad >= 0) setStep(firstBad);
      return;
    }

    setPending(true);
    try {
      const payload = normalize(values);
      const result = editing ? await updateProjectAction(props.projectId, payload) : await createProjectAction(payload);
      if (!result.ok) {
        setFormError(result.error);
        if (result.fieldErrors) {
          setErrors(result.fieldErrors);
          const bad = STEPS.findIndex((s) => s.fields.some((f) => result.fieldErrors?.[f]));
          if (bad >= 0) setStep(bad);
        }
        return;
      }
      const projectId = result.data.id;
      if (!editing && files.length > 0) {
        const failures = await uploadAll(projectId);
        if (failures.length > 0) {
          setUploadReport({ projectId, failures });
          return;
        }
      }
      router.push(`/projects/${projectId}`);
      router.refresh();
    } finally {
      setPending(false);
    }
  }

  const visible = editing ? STEPS.map((_, i) => i) : [step];
  const last = step === STEPS.length - 1;

  if (uploadReport) {
    return (
      <Card className="space-y-4 p-6">
        <Alert tone="warning" title="Projet créé, mais certains fichiers n'ont pas été envoyés">
          <ul className="list-disc pl-4">
            {uploadReport.failures.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
          Vous pourrez les ajouter depuis la page du projet.
        </Alert>
        <Button onClick={() => router.push(`/projects/${uploadReport.projectId}`)}>Ouvrir le projet</Button>
      </Card>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (editing || last) void submit();
        else next();
      }}
      className="space-y-6"
      noValidate
    >
      {!editing && (
        <ol className="flex items-center gap-2" aria-label="Étapes">
          {STEPS.map((s, i) => (
            <li key={s.title} className="flex flex-1 items-center gap-2">
              <button
                type="button"
                disabled={i > step}
                onClick={() => setStep(i)}
                aria-current={i === step ? "step" : undefined}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-xl border px-3 py-2 text-left text-sm transition-colors",
                  i === step ? "border-accent/50 bg-accent-soft text-foreground" : i < step ? "border-border text-muted hover:border-border-strong" : "border-border text-subtle",
                )}
              >
                <span className={cn("grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold", i < step ? "bg-success-soft text-success" : i === step ? "bg-accent text-accent-foreground" : "bg-white/6")}>
                  {i < step ? <Check className="size-3.5" aria-hidden /> : i + 1}
                </span>
                <span className="hidden truncate font-medium sm:inline">{s.title}</span>
              </button>
            </li>
          ))}
        </ol>
      )}

      {visible.includes(0) && (
        <Card className="space-y-5 p-5 sm:p-6">
          {editing && <h2 className="font-semibold">Produit</h2>}
          <Field label="Nom du produit" htmlFor="productName" error={errors.productName}>
            <Input id="productName" value={values.productName} onChange={(e) => set("productName", e.target.value)} placeholder="Gourde isotherme Nomad" maxLength={80} aria-invalid={Boolean(errors.productName)} />
          </Field>
          <Field label="Description" htmlFor="description" hint="Ce que c'est, ce que ça fait, ce qui le rend unique. Restez factuel : le concept s'appuie dessus." error={errors.description}>
            <Textarea id="description" value={values.description} onChange={(e) => set("description", e.target.value)} rows={4} maxLength={1200} aria-invalid={Boolean(errors.description)} />
          </Field>
          <Field label="Avantages clés" error={errors.benefits} hint="1 à 6 avantages concrets, un par ligne.">
            <div className="space-y-2">
              {values.benefits.map((b, i) => (
                <div key={i} className="flex gap-2">
                  <Input
                    aria-label={`Avantage ${i + 1}`}
                    value={b}
                    onChange={(e) => set("benefits", values.benefits.map((x, j) => (j === i ? e.target.value : x)))}
                    placeholder={i === 0 ? "Garde le froid 24 h" : "Un autre avantage"}
                    maxLength={120}
                  />
                  {values.benefits.length > 1 && (
                    <Button type="button" variant="ghost" size="sm" className="h-11 px-3" aria-label={`Supprimer l'avantage ${i + 1}`} onClick={() => set("benefits", values.benefits.filter((_, j) => j !== i))}>
                      <X className="size-4" aria-hidden />
                    </Button>
                  )}
                </div>
              ))}
              {values.benefits.length < 6 && (
                <Button type="button" variant="ghost" size="sm" icon={<Plus className="size-4" aria-hidden />} onClick={() => set("benefits", [...values.benefits, ""])}>
                  Ajouter un avantage
                </Button>
              )}
            </div>
          </Field>
        </Card>
      )}

      {visible.includes(1) && (
        <Card className="space-y-6 p-5 sm:p-6">
          {editing && <h2 className="font-semibold">Message & style</h2>}
          <Field label="Cible" htmlFor="targetAudience" hint="Qui doit se reconnaître dans la vidéo ?" error={errors.targetAudience}>
            <Textarea id="targetAudience" value={values.targetAudience} onChange={(e) => set("targetAudience", e.target.value)} rows={2} maxLength={400} aria-invalid={Boolean(errors.targetAudience)} placeholder="Sportifs urbains de 25 à 40 ans, sensibles à l'écologie" />
          </Field>
          <ChoiceGroup name="tone" legend="Ton" value={values.tone} onChange={(v) => set("tone", v)} options={TONES.map((t) => ({ value: t.id, label: t.label }))} columns="grid-cols-2 sm:grid-cols-3" compact error={errors.tone} />
          <ChoiceGroup name="visualStyle" legend="Style visuel" value={values.visualStyle} onChange={(v) => set("visualStyle", v)} options={VISUAL_STYLES.map((s) => ({ value: s.id, label: s.label }))} columns="grid-cols-2 sm:grid-cols-3" compact error={errors.visualStyle} />
          <div className="grid gap-5 sm:grid-cols-2">
            <Field label="Langue du concept et de la voix off" htmlFor="language" hint="Le prompt envoyé au modèle vidéo est rédigé en anglais.">
              <Select id="language" value={values.language} onChange={(e) => set("language", e.target.value as BriefInput["language"])}>
                {LANGUAGES.map((l) => (
                  <option key={l.id} value={l.id}>{l.label}</option>
                ))}
              </Select>
            </Field>
            <Field label="Appel à l'action" htmlFor="callToAction" hint="Optionnel." error={errors.callToAction}>
              <Input id="callToAction" value={values.callToAction ?? ""} onChange={(e) => set("callToAction", e.target.value)} placeholder="Commandez maintenant" maxLength={80} />
            </Field>
          </div>
          <Field label="Notes créatives" htmlFor="creativeNotes" hint="Optionnel : contraintes, ambiance, éléments à éviter…" error={errors.creativeNotes}>
            <Textarea id="creativeNotes" value={values.creativeNotes ?? ""} onChange={(e) => set("creativeNotes", e.target.value)} rows={3} maxLength={600} />
          </Field>
        </Card>
      )}

      {visible.includes(2) && (
        <Card className="space-y-6 p-5 sm:p-6">
          {editing && <h2 className="font-semibold">Format</h2>}
          <ChoiceGroup
            name="aspectRatio"
            legend="Format"
            value={values.aspectRatio}
            onChange={(v) => set("aspectRatio", v)}
            options={ASPECT_RATIOS.map((r) => ({ value: r, label: ASPECT_RATIO_META[r].label, description: ASPECT_RATIO_META[r].usage, visual: <AspectShape ratio={r} /> }))}
            columns="grid-cols-1 sm:grid-cols-3"
            error={errors.aspectRatio}
          />
          <ChoiceGroup
            name="durationSeconds"
            legend="Durée cible"
            hint="Chaque modèle ne supporte que certaines durées : vous choisirez la durée exacte au moment de générer."
            value={Number(values.durationSeconds)}
            onChange={(v) => set("durationSeconds", v)}
            options={DURATIONS.map((d) => ({ value: d, label: `${d} s` }))}
            columns="grid-cols-3 sm:grid-cols-6"
            compact
            error={errors.durationSeconds}
          />
          {!editing && (
            <div className="space-y-3 border-t border-border pt-6">
              <div>
                <h3 className="text-sm font-medium">Logo et photos produit</h3>
                <p className="mt-1 text-xs text-subtle">
                  PNG, JPEG ou WebP, 8 Mo maximum. Les photos aident le concept à décrire fidèlement votre produit et peuvent servir d&apos;image de référence si le modèle choisi le permet. Elles sont indispensables au montage gratuit (sans IA).
                </p>
              </div>
              <MediaPicker files={files} onAdd={addFiles} onRemove={removeFile} disabled={pending} />
              {fileErrors.length > 0 && (
                <Alert tone="danger">
                  <ul className="list-disc pl-4">{fileErrors.map((e) => <li key={e}>{e}</li>)}</ul>
                </Alert>
              )}
            </div>
          )}
        </Card>
      )}

      {formError && <Alert tone="danger">{formError}</Alert>}

      <div className="flex items-center justify-between gap-3">
        {!editing && step > 0 ? (
          <Button type="button" variant="ghost" onClick={() => setStep(step - 1)} icon={<ArrowLeft className="size-4" aria-hidden />}>
            Retour
          </Button>
        ) : (
          <span />
        )}
        {editing || last ? (
          <Button type="submit" size="lg" loading={pending} icon={editing ? undefined : <Sparkles className="size-4" aria-hidden />}>
            {editing ? "Enregistrer le brief" : files.length > 0 ? "Créer le projet et envoyer les images" : "Créer le projet"}
          </Button>
        ) : (
          <Button type="submit" size="lg">
            Continuer <ArrowRight className="size-4" aria-hidden />
          </Button>
        )}
      </div>
    </form>
  );
}
