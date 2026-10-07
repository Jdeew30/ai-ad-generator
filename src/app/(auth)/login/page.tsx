import type { Metadata } from "next";
import Link from "next/link";
import { AuthForm } from "@/components/auth/auth-form";
import { safeNextPath } from "@/lib/utils";

export const metadata: Metadata = { title: "Connexion" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Bon retour</h1>
      <p className="mt-1 mb-6 text-sm text-muted">Connectez-vous pour retrouver vos projets.</p>
      <AuthForm mode="login" next={safeNextPath(next)} />
      <p className="mt-6 text-center text-sm text-muted">
        Pas encore de compte ?{" "}
        <Link href="/register" className="font-medium text-accent hover:underline">
          Créer un compte
        </Link>
      </p>
    </>
  );
}
