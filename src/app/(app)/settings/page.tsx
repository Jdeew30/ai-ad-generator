import { Check, Minus } from "lucide-react";
import type { Metadata } from "next";
import { Alert, Badge, Card } from "@/components/ui/card";
import { getEnv } from "@/lib/env";
import { requireUser } from "@/lib/session";
import { describeConceptEngine } from "@/modules/concepts";
import { getProviders } from "@/modules/video-providers";

export const metadata: Metadata = { title: "Fournisseurs" };

export default async function SettingsPage() {
  await requireUser();
  const env = getEnv();
  const engine = describeConceptEngine();
  const providers = getProviders();

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Fournisseurs et configuration</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted">
          État réel de la configuration serveur. Les clés ne sont jamais affichées ni stockées en base : elles se définissent
          dans les variables d&apos;environnement.
        </p>
      </header>

      <section className="space-y-3" aria-labelledby="video">
        <h2 id="video" className="text-sm font-medium tracking-wide text-subtle uppercase">Génération vidéo</h2>
        <div className="grid gap-4">
          {providers.map((p) => {
            const ok = p.isConfigured();
            return (
              <Card key={p.id} className="p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <h3 className="font-semibold">{p.label}</h3>
                    {ok ? <Badge tone="success"><Check className="size-3" aria-hidden /> Configuré</Badge> : <Badge tone="warning"><Minus className="size-3" aria-hidden /> Non configuré</Badge>}
                  </div>
                  <code className="rounded-md bg-white/6 px-2 py-1 font-mono text-xs text-muted">{p.envVars.join(", ")}</code>
                </div>
                <ul className="mt-4 grid gap-2 sm:grid-cols-2">
                  {p.models.map((m) => (
                    <li key={m.id} className="rounded-xl border border-border bg-white/3 p-3 text-sm">
                      <p className="font-medium">{m.label}</p>
                      <p className="mt-0.5 text-xs text-subtle">
                        {m.capabilities.durations.join(" / ")} s · {m.capabilities.aspectRatios.join(", ")} · {m.capabilities.imageToVideo ? "image de référence" : "texte seul"}
                      </p>
                    </li>
                  ))}
                </ul>
              </Card>
            );
          })}
        </div>
        <Card className="p-5 text-sm leading-relaxed text-muted">
          <p className="font-medium text-foreground">À savoir</p>
          <p className="mt-1">
            Ces adaptateurs ont été écrits d&apos;après le code source des SDK officiels et testés contre un faux serveur
            local, pas contre les API réelles (aucune clé n&apos;était disponible lors du développement). Le premier test
            avec vos clés est la vraie validation : toute erreur du fournisseur s&apos;affichera telle quelle sur la
            génération concernée.
          </p>
        </Card>
      </section>

      <section className="space-y-3" aria-labelledby="concept">
        <h2 id="concept" className="text-sm font-medium tracking-wide text-subtle uppercase">Concept publicitaire</h2>
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <h3 className="font-semibold">{engine.source === "CLAUDE" ? "Claude (Anthropic)" : "Gabarit sans IA"}</h3>
              {engine.source === "CLAUDE" ? <Badge tone="success">Configuré</Badge> : <Badge tone="warning">ANTHROPIC_API_KEY absent</Badge>}
            </div>
            {engine.model && <code className="rounded-md bg-white/6 px-2 py-1 font-mono text-xs text-muted">{engine.model}</code>}
          </div>
          <p className="mt-3 text-sm text-muted">
            {engine.source === "CLAUDE"
              ? "Les concepts sont rédigés par Claude à partir du brief et des photos produit."
              : "Sans clé, le prompt vidéo est assemblé de façon déterministe à partir du brief : aucune rédaction créative."}
          </p>
        </Card>
      </section>

      <section className="space-y-3" aria-labelledby="guards">
        <h2 id="guards" className="text-sm font-medium tracking-wide text-subtle uppercase">Garde-fous</h2>
        <Card className="divide-y divide-border">
          <Row label="Générations par jour et par utilisateur" value={String(env.DAILY_GENERATION_LIMIT)} />
          <Row label="Générations simultanées par utilisateur" value="3" />
          <Row label="Inscriptions" value={env.ALLOW_SIGNUP ? "Ouvertes" : "Fermées"} />
          <Row label="Stockage" value="Disque local" />
          <Row label="Synchronisation en arrière-plan" value={env.CRON_SECRET ? "Endpoint cron activé" : "Désactivée"} />
        </Card>
        {!env.CRON_SECRET && (
          <Alert tone="warning" title="Sans cron, une vidéo terminée n'est récupérée qu'à l'ouverture de la page">
            Certains fournisseurs suppriment leurs fichiers de sortie après un délai court (parfois environ une heure). Si
            personne n&apos;ouvre le projet entre-temps, la vidéo sera perdue. Définissez CRON_SECRET et appelez
            GET /api/cron/sync-generations avec l&apos;en-tête Authorization: Bearer … toutes les minutes.
          </Alert>
        )}
      </section>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 px-5 py-3.5 text-sm">
      <span className="text-muted">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
