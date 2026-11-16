"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Legacy Tasks index — create/triage now live on Board. Detail stays at [id]. */
export default function TasksRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/workspace");
  }, [router]);

  return (
    <p className="font-mono text-sm text-muted">Redirecting to Board…</p>
  );
}
