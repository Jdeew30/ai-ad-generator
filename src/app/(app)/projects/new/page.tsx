import type { Metadata } from "next";
import { BriefForm } from "@/components/brief/brief-form";
import { requireUser } from "@/lib/session";

export const metadata: Metadata = { title: "Nouveau projet" };

export default async function NewProjectPage() {
  await requireUser();
  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Nouveau projet</h1>
        <p className="mt-1 text-sm text-muted">Trois étapes pour décrire votre produit. Vous pourrez tout modifier ensuite.</p>
      </header>
      <BriefForm mode="create" />
    </div>
  );
}
