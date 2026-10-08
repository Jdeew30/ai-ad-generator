import Link from "next/link";
import { Alert } from "@/components/ui/card";
import { getProviders, hasConfiguredAiProvider } from "@/modules/video-providers";

/**
 * Shown wherever generation is offered. States plainly when no generative-AI video
 * provider is configured. The free local montage may still be available, but it is
 * not AI, and the message says so.
 */
export function ProviderNotice() {
  if (hasConfiguredAiProvider()) return null;
  const providers = getProviders();
  const vars = providers.filter((p) => p.id !== "montage").flatMap((p) => p.envVars);
  const montageReady = providers.some((p) => p.id === "montage" && p.isConfigured());
  return (
    <Alert tone={montageReady ? "info" : "warning"} title="Aucun fournisseur d'IA vidéo n'est configuré">
      {montageReady
        ? "Aucune vidéo générée par IA n'est possible pour l'instant, mais vous pouvez créer gratuitement un montage de vos photos (sans IA). "
        : "Aucune vidéo ne peut être produite pour l'instant. "}
      Pour la génération par IA, définissez une clé côté serveur ({vars.join(", ")}).{" "}
      <Link href="/settings" className="font-medium text-foreground underline underline-offset-2">
        Voir la configuration
      </Link>
      .
    </Alert>
  );
}
