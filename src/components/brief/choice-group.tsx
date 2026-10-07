import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface Option<T> {
  value: T;
  label: string;
  description?: string;
  /** Optional leading visual (e.g. an aspect-ratio preview). */
  visual?: ReactNode;
}

interface ChoiceGroupProps<T extends string | number> {
  name: string;
  legend: string;
  hint?: string;
  value: T;
  onChange: (value: T) => void;
  options: readonly Option<T>[];
  /** Tailwind grid-cols classes. */
  columns?: string;
  compact?: boolean;
  error?: string;
}

/** Accessible radio group rendered as selectable cards (native radios, visually hidden). */
export function ChoiceGroup<T extends string | number>({
  name,
  legend,
  hint,
  value,
  onChange,
  options,
  columns = "grid-cols-2 sm:grid-cols-3",
  compact,
  error,
}: ChoiceGroupProps<T>) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{legend}</legend>
      <div className={cn("grid gap-2", columns)}>
        {options.map((o) => (
          <label key={String(o.value)} className="cursor-pointer">
            <input
              type="radio"
              name={name}
              value={String(o.value)}
              checked={value === o.value}
              onChange={() => onChange(o.value)}
              className="peer sr-only"
            />
            <span
              className={cn(
                "flex h-full gap-3 rounded-xl border border-border-strong bg-white/3 text-sm transition-colors",
                "hover:border-white/25 peer-checked:border-accent peer-checked:bg-accent-soft peer-focus-visible:outline-2 peer-focus-visible:outline-accent",
                compact ? "items-center justify-center px-3 py-2.5 text-center" : "flex-col p-3.5",
              )}
            >
              {o.visual}
              <span className="block">
                <span className="block font-medium">{o.label}</span>
                {o.description && <span className="mt-0.5 block text-xs text-subtle">{o.description}</span>}
              </span>
            </span>
          </label>
        ))}
      </div>
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-subtle">{hint}</p>
      ) : null}
    </fieldset>
  );
}
