"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  ClipboardList,
  Folder,
  LayoutDashboard,
  LayoutGrid,
  ListTodo,
  LogIn,
  MessageSquare,
  Siren,
  type LucideIcon,
} from "lucide-react";
import { SessionProvider, useSession } from "@/components/workspace/session";
import { RouvrMark } from "@/components/workspace/RouvrMark";
import { ThemeToggle } from "@/components/workspace/ThemeToggle";
import { shellGreeting } from "@/lib/greeting";

// Figma shell port (sidebar + topbar, 68px brand/topbar bands, 242px rail):
// contract IA in Figma order, theme tokens only, every control live — the
// context card and profile rows are static (no fake switcher/menu), and the
// only buttons are the sun/moon theme toggle plus Sign out (working logout)
// on an authenticated session.
const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/workspace", label: "Board", icon: LayoutGrid },
  { href: "/workspace/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/workspace/chat", label: "Chat", icon: MessageSquare },
  { href: "/workspace/planner", label: "Planner", icon: ClipboardList },
  { href: "/workspace/projects", label: "Projects", icon: Folder },
  { href: "/workspace/tasks", label: "Tasks", icon: ListTodo },
  { href: "/workspace/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/workspace/escalations", label: "Escalations", icon: Siren },
];

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

function isActive(pathname: string, href: string): boolean {
  if (href === "/workspace") return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavItem({
  href,
  label,
  icon: Icon,
  active,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
  active: boolean;
}) {
  // Figma refined nav metrics: 40px row, 12px gap, 12px medium label,
  // icon at 78% idle → full on hover/active, hover nudges 2px right
  // (motion-safe), active keeps an inset 2px brand bar with no layout shift.
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`group flex h-10 items-center gap-3 rounded-lg px-[11px] text-xs font-medium transition-[color,background-color,transform] duration-[160ms] ${
        active
          ? "nav-item-active bg-panel2 text-fog"
          : "text-dim hover:bg-panel2 hover:text-fog motion-safe:hover:translate-x-0.5"
      }`}
    >
      <Icon
        size={16}
        aria-hidden
        className={active ? "text-brand" : "opacity-[0.78] group-hover:opacity-100"}
      />
      <span className="truncate">{label}</span>
    </Link>
  );
}

function Avatar({ name, size = 30 }: { name: string; size?: number }) {
  const initial = (name.trim().charAt(0) || "?").toUpperCase();
  return (
    <span
      aria-hidden
      style={{ width: size, height: size }}
      className="grid shrink-0 place-items-center rounded-full border border-line2 bg-panel2 text-[11px] font-bold text-cyan2"
    >
      {initial}
    </span>
  );
}

function AppShell({ children }: { children: React.ReactNode }) {
  const { status, tenant, project, projects, username, logout } = useSession();
  const pathname = usePathname();
  const context = contextLabel(tenant, project, Math.max(0, projects.length - 1));
  const authed = status === "authenticated";
  // Real session user + real client date. suppressHydrationWarning: the
  // server render and the client clock can disagree across midnight.
  const greeting = shellGreeting(authed ? username : null, new Date());

  return (
    <div className="min-h-screen bg-abyss">
      <aside className="fixed inset-y-0 left-0 z-30 flex w-[242px] flex-col border-r border-line bg-abyss">
        <div className="flex h-[68px] flex-col justify-center gap-1 border-b border-line px-4">
          <Link href="/workspace" aria-label="Rouvr home" className="flex items-center gap-2.5">
            <RouvrMark size={32} />
            <span className="text-[20px] font-bold leading-none tracking-[-0.5px] text-white">
              rouvr
            </span>
          </Link>
          <span className="text-[10px] leading-snug text-muted">
            An autonomous AI colleague that turns conversations into accountable work.
          </span>
        </div>

        {context ? (
          <div className="mx-3 mt-3.5 rounded-[10px] border border-line bg-panel2 p-2.5">
            <p className="text-[8px] uppercase tracking-widest text-dim">Workspace</p>
            <p className="mt-1 truncate text-xs font-semibold text-fog">{context}</p>
          </div>
        ) : null}

        <nav aria-label="Workspace" className="flex flex-col gap-0.5 px-2.5 pt-4">
          <span className="px-2.5 pb-1.5 text-[9px] font-semibold uppercase tracking-widest text-dim">
            Workspace
          </span>
          {NAV.map((item) => (
            <NavItem
              key={item.href}
              href={item.href}
              label={item.label}
              icon={item.icon}
              active={isActive(pathname, item.href)}
            />
          ))}
          {status === "anonymous" ? (
            <NavItem
              href="/workspace/login"
              label="Login"
              icon={LogIn}
              active={isActive(pathname, "/workspace/login")}
            />
          ) : null}
        </nav>

        {authed ? (
          <div className="mt-auto border-t border-line p-3">
            <div className="flex items-center gap-2 px-1 py-1">
              <Avatar name={username ?? "?"} size={30} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[11px] font-semibold text-fog" data-profile-username>
                  {username ?? "unknown user"}
                </p>
                <p className="truncate text-[9px] text-dim">{tenant ?? ""}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void logout()}
              className="mt-2 h-9 w-full rounded-lg border border-line bg-panel2 text-[11px] font-medium text-dim transition-colors hover:border-line2 hover:text-fog"
            >
              Sign out
            </button>
          </div>
        ) : null}
      </aside>

      <div className="pl-[242px]">
        <header className="sticky top-0 z-20 flex h-[68px] items-center gap-3 border-b border-line bg-abyss/90 px-6 backdrop-blur">
          <div data-greeting>
            <p
              suppressHydrationWarning
              className="font-mono text-[10px] uppercase tracking-widest text-cyan2"
            >
              {greeting.eyebrow}
            </p>
            <p suppressHydrationWarning className="mt-0.5 text-[15px] font-semibold text-white">
              {greeting.title}
            </p>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <div className="text-right" data-project-heading>
              <p className="text-[9px] font-semibold uppercase tracking-widest text-dim">
                Active project
              </p>
              <p className="mt-0.5 max-w-56 truncate text-[15px] font-semibold leading-tight text-white">
                {project ?? "—"}
              </p>
            </div>
            <ThemeToggle />
            {authed && username ? (
              <>
                <span aria-hidden className="h-[22px] w-px bg-line" />
                <span className="flex items-center gap-2 text-[11px] text-dim">
                  <Avatar name={username} size={26} />
                  <span className="max-w-32 truncate">{username}</span>
                </span>
              </>
            ) : null}
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1600px] px-8 py-[30px]">{children}</main>
      </div>
    </div>
  );
}

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <div data-app-shell="workspace">
      <SessionProvider>
        <AppShell>{children}</AppShell>
      </SessionProvider>
    </div>
  );
}
