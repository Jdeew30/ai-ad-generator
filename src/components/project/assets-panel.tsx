"use client";

import { ImageIcon, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { MAX_PRODUCT_IMAGES, validateFile } from "@/components/brief/media-picker";
import { Alert } from "@/components/ui/card";
import { formatBytes } from "@/lib/utils";
import { deleteAssetAction } from "@/modules/assets/actions";

export interface AssetView {
  id: string;
  kind: "LOGO" | "PRODUCT_IMAGE";
  originalName: string;
  sizeBytes: number;
}

export function AssetsPanel({ projectId, assets }: { projectId: string; assets: AssetView[] }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [removing, startRemoving] = useTransition();
  const logoInput = useRef<HTMLInputElement>(null);
  const photoInput = useRef<HTMLInputElement>(null);

  const logo = assets.find((a) => a.kind === "LOGO");
  const photos = assets.filter((a) => a.kind === "PRODUCT_IMAGE");

  async function upload(files: File[], kind: AssetView["kind"]) {
    setError(null);
    const problems: string[] = [];
    setUploading(true);
    try {
      for (const file of files) {
        const problem = validateFile(file);
        if (problem) {
          problems.push(problem);
          continue;
        }
        const res = await fetch(`/api/projects/${projectId}/assets?kind=${kind}&name=${encodeURIComponent(file.name)}`, {
          method: "POST",
          body: file,
          headers: { "Content-Type": file.type },
        });
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { error?: string };
          problems.push(`${file.name} : ${body.error ?? "échec de l'envoi"}`);
        }
      }
    } catch {
      problems.push("Impossible de joindre le serveur.");
    } finally {
      setUploading(false);
      if (problems.length) setError(problems.join(" "));
      router.refresh();
    }
  }

  function remove(id: string) {
    setError(null);
    startRemoving(async () => {
      const result = await deleteAssetAction(id);
      if (!result.ok) setError(result.error);
      router.refresh();
    });
  }

  const busy = uploading || removing;

  return (
    <div className="space-y-4">
      <div className="grid gap-6 sm:grid-cols-[1fr_2fr]">
        <div className="space-y-2">
          <p className="text-sm font-medium">Logo</p>
          {logo ? (
            <Tile asset={logo} onRemove={() => remove(logo.id)} disabled={busy} wide />
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => logoInput.current?.click()}
              className="flex h-24 w-full items-center justify-center gap-2 rounded-xl border border-dashed border-border-strong text-sm text-subtle transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
            >
              <Plus className="size-4" aria-hidden /> Ajouter un logo
            </button>
          )}
          <input ref={logoInput} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1}
            onChange={(e) => { void upload(Array.from(e.target.files ?? []).slice(0, 1), "LOGO"); e.target.value = ""; }} />
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium">Photos produit <span className="font-normal text-subtle">({photos.length}/{MAX_PRODUCT_IMAGES})</span></p>
          <div className="flex flex-wrap gap-3">
            {photos.map((a) => (
              <Tile key={a.id} asset={a} onRemove={() => remove(a.id)} disabled={busy} />
            ))}
            {photos.length < MAX_PRODUCT_IMAGES && (
              <button
                type="button"
                disabled={busy}
                onClick={() => photoInput.current?.click()}
                aria-label="Ajouter des photos produit"
                className="grid size-24 place-items-center rounded-xl border border-dashed border-border-strong text-subtle transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
              >
                {uploading ? <span className="text-xs">Envoi…</span> : <Plus className="size-5" aria-hidden />}
              </button>
            )}
          </div>
          <input ref={photoInput} type="file" multiple accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1}
            onChange={(e) => { void upload(Array.from(e.target.files ?? []), "PRODUCT_IMAGE"); e.target.value = ""; }} />
        </div>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {assets.length === 0 && !error && (
        <p className="flex items-center gap-2 text-xs text-subtle">
          <ImageIcon className="size-3.5" aria-hidden /> Optionnel pour l&apos;IA. Au moins une photo produit est nécessaire pour le montage gratuit (sans IA).
        </p>
      )}
    </div>
  );
}

function Tile({ asset, onRemove, disabled, wide }: { asset: AssetView; onRemove: () => void; disabled?: boolean; wide?: boolean }) {
  return (
    <div className="group relative">
      {/* eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin asset */}
      <img
        src={`/api/assets/${asset.id}`}
        alt={asset.originalName}
        loading="lazy"
        className={wide ? "h-24 w-full rounded-xl border border-border bg-white/5 object-contain p-3" : "size-24 rounded-xl border border-border object-cover"}
      />
      <span className="sr-only">{asset.originalName}, {formatBytes(asset.sizeBytes)}</span>
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`Supprimer ${asset.originalName}`}
        className="absolute top-1.5 right-1.5 grid size-7 place-items-center rounded-lg bg-black/60 text-white opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 hover:bg-danger disabled:opacity-50 max-sm:opacity-100"
      >
        <Trash2 className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}
