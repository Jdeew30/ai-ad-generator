import { AppNav } from "@/components/app/app-nav";
import { UserMenu } from "@/components/app/user-menu";
import { Logo } from "@/components/logo";
import { requireUser } from "@/lib/session";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const user = await requireUser();

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15.5rem_1fr]">
      <aside className="border-b border-border bg-surface/50 backdrop-blur lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col lg:justify-between lg:border-r lg:border-b-0 lg:p-4">
        <div className="flex items-center justify-between gap-4 px-4 py-3 lg:block lg:p-0">
          <Logo href="/dashboard" className="lg:mb-8 lg:px-3 lg:pt-2" />
          <div className="lg:hidden">
            <UserMenu name={user.name} email={user.email} />
          </div>
        </div>
        <div className="overflow-x-auto px-3 pb-3 lg:flex-1 lg:overflow-visible lg:p-0">
          <AppNav />
        </div>
        <div className="hidden lg:block">
          <UserMenu name={user.name} email={user.email} />
        </div>
      </aside>
      <main className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-8 lg:py-12">{children}</main>
    </div>
  );
}
