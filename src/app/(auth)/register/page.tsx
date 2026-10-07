import type { Metadata } from "next";
import Link from "next/link";
import { AuthForm } from "@/components/auth/auth-form";
import { Alert } from "@/components/ui/card";
import { getEnv } from "@/lib/env";
import { safeNextPath } from "@/lib/utils";

export const metadata: Metadata = { title: "Créer un compte" };

export default async function RegisterPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  const open = getEnv().ALLOW_SIGNUP;

  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Créer un compte</h1>
      <p className="mt-1 mb-6 text-sm text-muted">Quelques secondes pour commencer votre premier brief.</p>
      {open ? (
        <AuthForm mode="register" next={safeNextPath(next)} />
      ) : (
        <Alert tone="warning" title="Inscriptions fermées">
          L&apos;administrateur de cette instance a désactivé la création de comptes.
        </Alert>
      )}
      <p className="mt-6 text-center text-sm text-muted">
        Déjà inscrit ?{" "}
        <Link href="/login" className="font-medium text-accent hover:underline">
          Se connecter
        </Link>
      </p>
    </>
  );
}
