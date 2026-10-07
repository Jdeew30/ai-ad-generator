"use client";

import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/card";

export default function AppError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="mx-auto max-w-lg space-y-4 py-16">
      <Alert tone="danger" title="Une erreur est survenue">
        La page n&apos;a pas pu être chargée. Réessayez ; si le problème persiste, consultez les journaux du serveur.
      </Alert>
      <Button variant="secondary" onClick={reset}>Réessayer</Button>
    </div>
  );
}
