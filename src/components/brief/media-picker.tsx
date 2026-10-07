"use client";

import { ImageIcon, Plus, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { formatBytes } from "@/lib/utils";

export const MAX_FILE_BYTES = 8 * 1024 * 1024;
export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"];
export const MAX_PRODUCT_IMAGES = 5;

export interface PendingFile {
  id: string;
  file: File;
  kind: "LOGO" | "PRODUCT_IMAGE";
  previewUrl: string;
}

/** Early feedback only — the server re-validates type (magic bytes) and size. */
export function validateFile(file: File): string | null {
  if (!ACCEPTED_TYPES.includes(file.type)) return `« ${file.name} » : format non supporté (PNG, JPEG ou WebP).`;
  if (file.size > MAX_FILE_BYTES) return `« ${file.name} » : fichier trop volumineux (8 Mo maximum).`;
  if (file.size === 0) return `« ${file.name} » : fichier vide.`;
  return null;
}

interface MediaPickerProps {
  files: PendingFile[];
  onAdd: (files: File[], kind: PendingFile["kind"]) => void;
  onRemove: (id: string) => void;
  disabled?: boolean;
}

export function MediaPicker({ files, onAdd, onRemove, disabled }: MediaPickerProps) {
  const logoInput = useRef<HTMLInputElement>(null);
  const photoInput = useRef<HTMLInputElement>(null);
  const urlsRef = useRef<string[]>([]);

  // Revoke object URLs we created when the picker goes away.
  useEffect(() => {
    const urls = urlsRef;
    return () => urls.current.forEach((u) => URL.revokeObjectURL(u));
  }, []);
  useEffect(() => {
    urlsRef.current = files.map((f) => f.previewUrl);
  }, [files]);

  const logo = files.find((f) => f.kind === "LOGO");
  const photos = files.filter((f) => f.kind === "PRODUCT_IMAGE");

  return (
    <div className="grid gap-5 sm:grid-cols-2">
      <div className="space-y-2">
        <p className="text-sm font-medium">Logo <span className="font-normal text-subtle">(optionnel)</span></p>
        {logo ? (
          <PreviewTile file={logo} onRemove={() => onRemove(logo.id)} disabled={disabled} />
        ) : (
          <Button type="button" variant="secondary" disabled={disabled} onClick={() => logoInput.current?.click()} icon={<Plus className="size-4" aria-hidden />}>
            Ajouter un logo
          </Button>
        )}
        <input
          ref={logoInput}
          type="file"
          accept={ACCEPTED_TYPES.join(",")}
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            onAdd(Array.from(e.target.files ?? []).slice(0, 1), "LOGO");
            e.target.value = "";
          }}
        />
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">
          Photos produit <span className="font-normal text-subtle">({photos.length}/{MAX_PRODUCT_IMAGES})</span>
        </p>
        <div className="flex flex-wrap gap-2">
          {photos.map((p) => (
            <PreviewTile key={p.id} file={p} onRemove={() => onRemove(p.id)} disabled={disabled} compact />
          ))}
          {photos.length < MAX_PRODUCT_IMAGES && (
            <button
              type="button"
              disabled={disabled}
              onClick={() => photoInput.current?.click()}
              className="grid size-20 place-items-center rounded-xl border border-dashed border-border-strong text-subtle transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
              aria-label="Ajouter des photos produit"
            >
              <Plus className="size-5" aria-hidden />
            </button>
          )}
        </div>
        <input
          ref={photoInput}
          type="file"
          accept={ACCEPTED_TYPES.join(",")}
          multiple
          className="sr-only"
          tabIndex={-1}
          onChange={(e) => {
            onAdd(Array.from(e.target.files ?? []), "PRODUCT_IMAGE");
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}

function PreviewTile({ file, onRemove, disabled, compact }: { file: PendingFile; onRemove: () => void; disabled?: boolean; compact?: boolean }) {
  return (
    <div className="group relative w-fit max-w-full">
      {/* eslint-disable-next-line @next/next/no-img-element -- local blob preview */}
      <img
        src={file.previewUrl}
        alt={file.file.name}
        className={compact ? "size-20 rounded-xl border border-border object-cover" : "h-20 max-w-full rounded-xl border border-border bg-white/5 object-contain p-2"}
      />
      {!compact && (
        <p className="mt-1 flex items-center gap-1 text-xs text-subtle">
          <ImageIcon className="size-3" aria-hidden /> {file.file.name} · {formatBytes(file.file.size)}
        </p>
      )}
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`Retirer ${file.file.name}`}
        className="absolute -top-2 -right-2 grid size-6 place-items-center rounded-full border border-border-strong bg-raised text-muted shadow transition-colors hover:text-danger disabled:opacity-50"
      >
        <X className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}
