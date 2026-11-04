"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  clearCsrfToken,
  clearSessionUsername,
  sessionUsername,
  ws,
  type ProjectRow,
} from "@/lib/api/workspace";

export type SessionStatus = "loading" | "authenticated" | "anonymous";

interface SessionValue {
  status: SessionStatus;
  projects: ProjectRow[];
  tenant: string | null;
  project: string | null;
  setProject: (name: string) => void;
  username: string | null;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

// Single session source for the whole /workspace tree. Auth state comes
// from the httponly session cookie (probed via GET /api/projects, which
// also supplies the tenant/project context); pages must not keep their own
// copy of it.
const PROJECT_KEY = "rouvr.project";

function storedProject(): string | null {
  try {
    return localStorage.getItem(PROJECT_KEY);
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [activeProject, setActiveProject] = useState<string | null>(null);
  const [username, setUsername] = useState<string | null>(null);
  const seq = useRef(0);

  // Active project: the stored choice wins when it is still in the live
  // list, otherwise the first project. Header switcher writes it.
  const setProject = useCallback((name: string) => {
    // No membership guard: callers offer known names, and post-rename
    // callers re-point at a name the pre-refresh list does not contain yet.
    setActiveProject(name);
    try {
      localStorage.setItem(PROJECT_KEY, name);
    } catch {
      /* private mode: choice lasts for the session only */
    }
  }, []);

  const refresh = useCallback(async () => {
    const run = ++seq.current;
    await (async () => {
      try {
        const { data, response } = await ws.GET("/api/projects");
        if (run !== seq.current) return;
        if (response.status === 401) {
          setProjects([]);
          setUsername(null);
          clearSessionUsername();
          setStatus("anonymous");
          return;
        }
        if (!response.ok || !data) return;
        setProjects(data.projects);
        // Reconcile the active project with the live list: keep the
        // current choice when it still exists, else the stored choice,
        // else the first project.
        const stored = storedProject();
        const known = (name: string | null) =>
          !!name && data.projects.some((p: ProjectRow) => p.project === name);
        setActiveProject((prev) =>
          known(prev) ? prev : known(stored) ? stored : (data.projects[0]?.project ?? null),
        );
        // The stored login-reply username is only trusted alongside a live
        // session: this probe just returned 200 for the session cookie.
        setUsername(sessionUsername());
        setStatus("authenticated");
      } catch {
        // Network failure: leave the last known status in place.
      }
    })();
  }, []);

  // Probe once on mount and re-probe on navigation (e.g. after the login
  // page pushes to /workspace, the cookie is already set by then).
  useEffect(() => {
    refresh();
  }, [pathname, refresh]);

  const logout = useCallback(async () => {
    await ws.POST("/api/auth/logout").catch(() => undefined);
    clearCsrfToken();
    clearSessionUsername();
    setProjects([]);
    setUsername(null);
    setStatus("anonymous");
    router.push("/workspace/login");
  }, [router]);

  const value = useMemo<SessionValue>(() => {
    const tenant = projects.length > 0 ? projects[0].tenant : null;
    // Active project drives the header, sidebar context, and every page
    // filter; it is the stored choice when live, else the first project.
    const project =
      activeProject && projects.some((p) => p.project === activeProject)
        ? activeProject
        : (projects[0]?.project ?? null);
    return { status, projects, tenant, project, setProject, username, refresh, logout };
  }, [status, projects, activeProject, setProject, username, refresh, logout]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>");
  return ctx;
}
