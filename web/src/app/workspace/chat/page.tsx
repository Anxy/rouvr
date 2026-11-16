"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ChatSurface } from "@/components/workspace/ChatSurface";
import { PlansInbox } from "@/components/workspace/PlansInbox";

function SupervisorHome() {
  const params = useSearchParams();
  const justOnboarded = params.get("onboarded") === "1";
  // Chat owns the main column; Plans sits beside it on wide screens and
  // in a capped scroll region below on narrow — never under the composer.
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row lg:gap-6">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <ChatSurface mode="supervisor" justOnboarded={justOnboarded} />
      </div>
      <aside
        data-lenis-prevent
        aria-label="Plans"
        className="flex max-h-[36vh] min-h-0 w-full shrink-0 flex-col overflow-hidden border-t border-line pt-3 lg:max-h-none lg:w-[min(380px,36%)] lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0"
      >
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <PlansInbox />
        </div>
      </aside>
    </div>
  );
}

/** Single Supervisor home: chat + Plans inbox (approve → Start delivery). */
export default function ChatPage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Suspense fallback={<p className="text-sm text-dim">Loading…</p>}>
        <SupervisorHome />
      </Suspense>
    </div>
  );
}
