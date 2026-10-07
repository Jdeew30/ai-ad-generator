import { CircleAlert, CircleCheck, Info, TriangleAlert } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-2xl border border-border bg-surface/80 shadow-[0_1px_0_0_oklch(1_0_0/0.04)_inset]", className)}
      {...props}
    />
  );
}

interface SectionProps {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  step?: number;
  children: ReactNode;
  className?: string;
}

/** Titled card used for each stage of a project (brief, media, concept, video). */
export function Section({ title, description, action, step, children, className }: SectionProps) {
  return (
    <Card className={cn("p-5 sm:p-6", className)}>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {step !== undefined && (
            <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">
              {step}
            </span>
          )}
          <div>
            <h2 className="text-base font-semibold tracking-tight">{title}</h2>
            {description && <p className="mt-1 max-w-prose text-sm text-muted">{description}</p>}
          </div>
        </div>
        {action}
      </div>
      {children}
    </Card>
  );
}

type Tone = "neutral" | "accent" | "success" | "warning" | "danger";

const badgeTones: Record<Tone, string> = {
  neutral: "bg-white/6 text-muted border-border",
  accent: "bg-accent-soft text-accent border-accent/25",
  success: "bg-success-soft text-success border-success/25",
  warning: "bg-warning-soft text-warning border-warning/25",
  danger: "bg-danger-soft text-danger border-danger/25",
};

export function Badge({ tone = "neutral", className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        badgeTones[tone],
        className,
      )}
      {...props}
    />
  );
}

const alertTones: Record<"info" | "success" | "warning" | "danger", { box: string; icon: ReactNode }> = {
  info: { box: "border-accent/25 bg-accent-soft", icon: <Info className="size-4 text-accent" aria-hidden /> },
  success: { box: "border-success/25 bg-success-soft", icon: <CircleCheck className="size-4 text-success" aria-hidden /> },
  warning: { box: "border-warning/30 bg-warning-soft", icon: <TriangleAlert className="size-4 text-warning" aria-hidden /> },
  danger: { box: "border-danger/30 bg-danger-soft", icon: <CircleAlert className="size-4 text-danger" aria-hidden /> },
};

export function Alert({
  tone = "info",
  title,
  children,
  className,
}: {
  tone?: keyof typeof alertTones;
  title?: string;
  children?: ReactNode;
  className?: string;
}) {
  const t = alertTones[tone];
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cn("flex gap-3 rounded-xl border p-3.5 text-sm", t.box, className)}>
      <span className="mt-0.5 shrink-0">{t.icon}</span>
      <div className="space-y-1">
        {title && <p className="font-medium text-foreground">{title}</p>}
        {children && <div className="text-muted">{children}</div>}
      </div>
    </div>
  );
}
