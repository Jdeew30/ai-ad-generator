import { redirect } from "next/navigation";
import { Logo } from "@/components/logo";
import { getSessionUser } from "@/lib/session";

export default async function AuthLayout({ children }: { children: React.ReactNode }) {
  // Already signed in: skip the auth screens.
  if (await getSessionUser()) redirect("/dashboard");

  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-5 py-12">
      <div className="mb-8 flex justify-center">
        <Logo />
      </div>
      <div className="rounded-2xl border border-border bg-surface/80 p-6 shadow-2xl shadow-black/30 sm:p-8">{children}</div>
    </div>
  );
}
