"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { authClient } from "@/lib/auth-client";

export function UserMenu({ name, email }: { name: string; email: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function signOut() {
    setPending(true);
    try {
      await authClient.signOut();
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <div className="flex items-center gap-3 lg:flex-col lg:items-stretch">
      <div className="min-w-0 lg:px-3">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="truncate text-xs text-subtle">{email}</p>
      </div>
      <Button variant="ghost" size="sm" onClick={signOut} loading={pending} icon={<LogOut className="size-4" aria-hidden />} className="lg:justify-start">
        <span className="max-lg:sr-only">Déconnexion</span>
      </Button>
    </div>
  );
}
