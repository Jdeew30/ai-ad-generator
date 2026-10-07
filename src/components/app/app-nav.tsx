"use client";

import { FolderOpen, Plus, Server } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const items = [
  { href: "/dashboard", label: "Projets", icon: FolderOpen, match: (p: string) => p === "/dashboard" || (p.startsWith("/projects") && p !== "/projects/new") },
  { href: "/projects/new", label: "Nouveau projet", icon: Plus, match: (p: string) => p === "/projects/new" },
  { href: "/settings", label: "Fournisseurs", icon: Server, match: (p: string) => p.startsWith("/settings") },
] as const;

export function AppNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Navigation principale" className="flex gap-1 lg:flex-col">
      {items.map(({ href, label, icon: Icon, match }) => {
        const active = match(pathname);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition-colors",
              active ? "bg-accent-soft text-accent" : "text-muted hover:bg-white/6 hover:text-foreground",
            )}
          >
            <Icon className="size-4" aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
