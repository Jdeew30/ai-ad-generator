import { ButtonLink } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto grid min-h-dvh max-w-md place-items-center px-5 text-center">
      <div className="space-y-4">
        <p className="text-sm font-medium text-accent">404</p>
        <h1 className="text-2xl font-semibold tracking-tight">Page introuvable</h1>
        <p className="text-sm text-muted">Cette page n&apos;existe pas, ou vous n&apos;y avez pas accès.</p>
        <ButtonLink href="/dashboard">Retour aux projets</ButtonLink>
      </div>
    </div>
  );
}
