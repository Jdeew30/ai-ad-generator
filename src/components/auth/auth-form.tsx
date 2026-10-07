"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/form";
import { authClient } from "@/lib/auth-client";

type Mode = "login" | "register";

function friendlyError(error: { status?: number; code?: string; message?: string }): string {
  if (error.status === 429) return "Trop de tentatives. Réessayez dans une minute.";
  const code = error.code ?? "";
  if (code.includes("INVALID_EMAIL_OR_PASSWORD") || code.includes("INVALID_PASSWORD")) return "E-mail ou mot de passe incorrect.";
  if (code.includes("USER_ALREADY_EXISTS")) return "Un compte existe déjà avec cet e-mail.";
  if (code.includes("PASSWORD_TOO_SHORT")) return "Le mot de passe doit contenir au moins 10 caractères.";
  if (code.includes("PASSWORD_TOO_LONG")) return "Le mot de passe est trop long.";
  if (code.includes("INVALID_EMAIL")) return "Adresse e-mail invalide.";
  if (code.includes("SIGN_UP_DISABLED") || code.includes("EMAIL_PASSWORD_SIGN_UP_DISABLED")) return "Les inscriptions sont fermées.";
  return error.message || "Une erreur est survenue. Réessayez.";
}

export function AuthForm({ mode, next }: { mode: Mode; next: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    const name = String(form.get("name") ?? "").trim();

    setPending(true);
    try {
      const result =
        mode === "register"
          ? await authClient.signUp.email({ name: name || email.split("@")[0] || "Utilisateur", email, password })
          : await authClient.signIn.email({ email, password });
      if (result.error) {
        setError(friendlyError(result.error));
        return;
      }
      router.replace(next as never);
      router.refresh();
    } catch {
      setError("Impossible de joindre le serveur. Réessayez.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      {mode === "register" && (
        <Field label="Nom" htmlFor="name">
          <Input id="name" name="name" autoComplete="name" placeholder="Camille Martin" required maxLength={80} />
        </Field>
      )}
      <Field label="E-mail" htmlFor="email">
        <Input id="email" name="email" type="email" autoComplete="email" placeholder="vous@entreprise.com" required />
      </Field>
      <Field
        label="Mot de passe"
        htmlFor="password"
        hint={mode === "register" ? "10 caractères minimum." : undefined}
      >
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete={mode === "register" ? "new-password" : "current-password"}
          required
          minLength={mode === "register" ? 10 : undefined}
        />
      </Field>
      {error && <Alert tone="danger">{error}</Alert>}
      <Button type="submit" loading={pending} className="w-full" size="lg">
        {mode === "register" ? "Créer mon compte" : "Se connecter"}
      </Button>
    </form>
  );
}
