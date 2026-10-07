import Link from "next/link";
import { cn } from "@/lib/utils";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-8", className)} aria-hidden>
      <defs>
        <linearGradient id="logo-g" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop stopColor="oklch(0.78 0.17 300)" />
          <stop offset="1" stopColor="oklch(0.62 0.22 285)" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#logo-g)" />
      <path d="M12.5 9.8v12.4a1 1 0 0 0 1.5.86l10-6.2a1 1 0 0 0 0-1.7l-10-6.2a1 1 0 0 0-1.5.84Z" fill="white" fillOpacity=".95" />
      <path d="M7 7.5l.9 2 2 .9-2 .9L7 13.3l-.9-2-2-.9 2-.9.9-2Z" fill="white" fillOpacity=".7" />
    </svg>
  );
}

export function Logo({ href = "/", className }: { href?: "/" | "/dashboard"; className?: string }) {
  return (
    <Link href={href} className={cn("inline-flex items-center gap-2.5 font-semibold tracking-tight whitespace-nowrap", className)}>
      <LogoMark />
      <span>
        AI Ad <span className="text-muted">Studio</span>
      </span>
    </Link>
  );
}
