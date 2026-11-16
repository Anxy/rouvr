"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Legacy Execute route — plan delivery now lives on Board (?plan=). */
export default function ExecuteRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const plan = params.get("plan");
    const target = plan
      ? `/workspace?plan=${encodeURIComponent(plan)}`
      : "/workspace";
    router.replace(target);
  }, [router]);

  return (
    <p className="font-mono text-sm text-muted">Redirecting to Board…</p>
  );
}
