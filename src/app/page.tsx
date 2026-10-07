import { ArrowRight, Ban, Check, FileText, Film, Sparkles } from "lucide-react";
import { Logo } from "@/components/logo";
import { ButtonLink } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { getSessionUser } from "@/lib/session";

const steps = [
  {
    icon: FileText,
    title: "1 · Le brief",
    text: "Produit, description, avantages, cible, ton, style visuel, format et durée. Ajoutez votre logo et vos photos produit.",
  },
  {
    icon: Sparkles,
    title: "2 · Le concept",
    text: "Accroche, scènes minutées, script de voix off et un prompt vidéo consolidé que vous pouvez relire et modifier avant tout envoi.",
  },
  {
    icon: Film,
    title: "3 · La vidéo",
    text: "Le prompt part chez le fournisseur que vous avez connecté. Le suivi est celui du vrai job, et le fichier n'est proposé au téléchargement qu'une fois réellement reçu.",
  },
];

const does = [
  "Un clip par génération, avec la durée et le format réellement supportés par le modèle choisi",
  "Le statut réel du job et les erreurs du fournisseur, affichés tels quels",
  "Un plafond quotidien et un nombre de jobs simultanés limité pour protéger votre budget API",
  "Vos fichiers copiés dans votre propre stockage dès la fin du rendu",
];

const doesNot = [
  "Pas de montage multi-clips, de musique ni de voix off générées : le script est fourni pour votre montage",
  "Pas de paiement ni de crédits intégrés dans cette version",
  "Aucune vidéo « de démonstration » : sans clé API de fournisseur, la génération reste désactivée",
];

export default async function HomePage() {
  const user = await getSessionUser();

  return (
    <div className="mx-auto flex min-h-dvh max-w-6xl flex-col px-5 sm:px-8">
      <header className="flex h-20 items-center justify-between">
        <Logo />
        <nav className="flex items-center gap-2">
          {user ? (
            <ButtonLink href="/dashboard" size="sm">
              Ouvrir le studio
            </ButtonLink>
          ) : (
            <>
              <ButtonLink href="/login" variant="ghost" size="sm">
                Connexion
              </ButtonLink>
              <ButtonLink href="/register" size="sm">
                Créer un compte
              </ButtonLink>
            </>
          )}
        </nav>
      </header>

      <main className="flex-1">
        <section className="py-16 sm:py-24">
          <div className="max-w-3xl">
            <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
              <Sparkles className="size-3.5" aria-hidden /> Brief → Concept → Vidéo
            </p>
            <h1 className="text-4xl leading-[1.05] font-semibold tracking-tight text-balance sm:text-6xl">
              Des vidéos publicitaires à partir d&apos;un simple brief produit.
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-relaxed text-muted text-pretty">
              Décrivez votre produit. AI Ad Studio rédige le concept, prépare le prompt, puis pilote le fournisseur de
              génération vidéo IA que vous avez connecté. Pas de promesse magique : chaque vidéo est rendue par un vrai
              modèle, avec votre clé API.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <ButtonLink href={user ? "/projects/new" : "/register"} size="lg">
                {user ? "Nouveau projet" : "Commencer"} <ArrowRight className="size-4" aria-hidden />
              </ButtonLink>
              <ButtonLink href={user ? "/dashboard" : "/login"} variant="secondary" size="lg">
                {user ? "Mes projets" : "Se connecter"}
              </ButtonLink>
            </div>
          </div>
        </section>

        <section aria-labelledby="how" className="pb-16">
          <h2 id="how" className="mb-6 text-sm font-medium tracking-wide text-subtle uppercase">
            Comment ça marche
          </h2>
          <div className="grid gap-4 md:grid-cols-3">
            {steps.map(({ icon: Icon, title, text }) => (
              <Card key={title} className="p-6">
                <span className="mb-4 grid size-10 place-items-center rounded-xl bg-accent-soft text-accent">
                  <Icon className="size-5" aria-hidden />
                </span>
                <h3 className="mb-2 font-semibold tracking-tight">{title}</h3>
                <p className="text-sm leading-relaxed text-muted">{text}</p>
              </Card>
            ))}
          </div>
        </section>

        <section aria-labelledby="providers" className="pb-16">
          <Card className="p-6 sm:p-8">
            <h2 id="providers" className="text-xl font-semibold tracking-tight">
              Votre fournisseur, votre clé
            </h2>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
              AI Ad Studio ne génère pas de vidéo lui-même : il orchestre des fournisseurs tiers. Cette version sait
              piloter OpenAI Sora, Replicate et fal.ai. Un fournisseur n&apos;apparaît comme utilisable que si sa clé est
              configurée côté serveur, et l&apos;architecture permet d&apos;en ajouter d&apos;autres sans toucher au reste
              de l&apos;application.
            </p>
          </Card>
        </section>

        <section aria-labelledby="honest" className="pb-24">
          <h2 id="honest" className="mb-6 text-sm font-medium tracking-wide text-subtle uppercase">
            Ce que fait — et ne fait pas — cette version
          </h2>
          <div className="grid gap-4 md:grid-cols-2">
            <Card className="p-6">
              <ul className="space-y-3 text-sm text-muted">
                {does.map((t) => (
                  <li key={t} className="flex gap-3">
                    <Check className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
                    {t}
                  </li>
                ))}
              </ul>
            </Card>
            <Card className="p-6">
              <ul className="space-y-3 text-sm text-muted">
                {doesNot.map((t) => (
                  <li key={t} className="flex gap-3">
                    <Ban className="mt-0.5 size-4 shrink-0 text-subtle" aria-hidden />
                    {t}
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </section>
      </main>

      <footer className="border-t border-border py-8 text-sm text-subtle">AI Ad Studio — MVP</footer>
    </div>
  );
}
