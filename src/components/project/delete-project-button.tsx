"use client";

import { Trash2 } from "lucide-react";
import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { deleteProjectAction } from "@/modules/projects/actions";

export function DeleteProjectButton({ projectId, hasActiveJobs }: { projectId: string; hasActiveJobs: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  if (!confirming) {
    return (
      <Button variant="ghost" size="sm" onClick={() => setConfirming(true)} icon={<Trash2 className="size-4" aria-hidden />}>
        Supprimer
      </Button>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Confirmer la suppression">
      <span className="text-xs text-muted">
        {hasActiveJobs ? "Des générations sont en cours : elles seront annulées si le fournisseur le permet. " : ""}
        Supprime le projet, ses images et ses vidéos.
      </span>
      <Button
        variant="danger"
        size="sm"
        loading={pending}
        onClick={() =>
          startTransition(async () => {
            const result = await deleteProjectAction(projectId);
            // On success the action redirects; we only get here on failure.
            if (result && !result.ok) setError(result.error);
          })
        }
      >
        Supprimer définitivement
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setConfirming(false)} disabled={pending}>
        Annuler
      </Button>
      {error && <span role="alert" className="text-xs text-danger">{error}</span>}
    </div>
  );
}
