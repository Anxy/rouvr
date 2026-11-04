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
  username: string | null;
  refresh: () => void;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

// Single session source for the whole /workspace tree. Auth state comes
// from the httponly session cookie (probed via GET /api/projects, which
// also supplies the tenant/project context); pages must not keep their own
// copy of it.
export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [username, setUsername] = useState<string | null>(null);
  const seq = useRef(0);

  const refresh = useCallback(() => {
    const run = ++seq.current;
    void (async () => {
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
    const project = projects.length > 0 ? projects[0].project : null;
    return { status, projects, tenant, project, username, refresh, logout };
  }, [status, projects, username, refresh, logout]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>");
  return ctx;
}
