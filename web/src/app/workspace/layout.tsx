"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { SessionProvider, useSession } from "@/components/workspace/session";

function contextLabel(
  tenant: string | null,
  project: string | null,
  extraProjects: number,
): string | null {
  if (!tenant) return null;
  if (!project) return tenant;
  if (extraProjects > 0) return `${tenant} / ${project} +${extraProjects} more`;
  return `${tenant} / ${project}`;
}

function AppHeader() {
  const { status, tenant, project, projects, logout } = useSession();
  const pathname = usePathname();
  const context = contextLabel(tenant, project, Math.max(0, projects.length - 1));

  const tab = (href: string, label: string) => {
    const active = href === "/workspace" ? pathname === href : pathname.startsWith(href);
    return (
      <Link
        key={href}
        href={href}
        aria-current={active ? "page" : undefined}
        className={`rounded-full px-4 py-1.5 text-sm transition-colors hover:bg-steel/30 hover:text-white ${
          active ? "bg-steel/30 text-white" : "text-cloud"
        }`}
      >
        {label}
      </Link>
    );
  };

  return (
    <div className="mb-8">
      <div className="flex flex-wrap items-center gap-3 pb-4">
        <Link href="/workspace" className="flex items-baseline gap-2">
          <span className="font-mono text-xs uppercase tracking-widest text-white">
            Rouvr
          </span>
          <span className="hidden text-[11px] text-muted sm:inline">
            An autonomous AI colleague that turns conversations into accountable work.
          </span>
        </Link>
        {context ? (
          <span className="font-mono text-xs text-muted">{context}</span>
        ) : null}
        {status === "authenticated" ? (
          <button
            type="button"
            onClick={() => void logout()}
            className="ml-auto rounded-full px-4 py-1.5 text-sm text-muted transition-colors hover:bg-steel/30 hover:text-white"
          >
            Sign out
          </button>
        ) : null}
      </div>
      <nav className="flex items-center gap-2 border-b border-steel/40 pb-4">
        {tab("/workspace", "Board")}
        {tab("/workspace/chat", "Chat")}
        {tab("/workspace/knowledge", "Knowledge")}
        {status === "anonymous" ? (
          <Link
            href="/workspace/login"
            className="ml-auto rounded-full px-4 py-1.5 text-sm text-muted transition-colors hover:bg-steel/30 hover:text-white"
          >
            Login
          </Link>
        ) : null}
      </nav>
    </div>
  );
}

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <div data-app-shell="workspace" className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
      <SessionProvider>
        <AppHeader />
        {children}
      </SessionProvider>
    </div>
  );
}
