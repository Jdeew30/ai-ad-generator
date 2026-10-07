import Link from "next/link";
import { Alert } from "@/components/ui/card";
import { hasConfiguredProvider, getProviders } from "@/modules/video-providers";

/**
 * Shown wherever generation is offered. States plainly when no video provider
 * is configured — in that case no video can be produced, and nothing pretends otherwise.
 */
export function ProviderNotice() {
  if (hasConfiguredProvider()) return null;
  const vars = getProviders().flatMap((p) => p.envVars);
  return (
    <Alert tone="warning" title="Aucun fournisseur vidéo n'est configuré">
      Vous pouvez préparer des briefs et des concepts, mais aucune vidéo ne peut être générée tant qu&apos;une clé API n&apos;est
      définie côté serveur ({vars.join(", ")}).{" "}
      <Link href="/settings" className="font-medium text-foreground underline underline-offset-2">
        Voir la configuration
      </Link>
      .
    </Alert>
  );
}
