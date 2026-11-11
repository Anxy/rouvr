"use client";

import { ChatSurface } from "@/components/workspace/ChatSurface";

// Supervisor chat — project comes from the header switcher; history loads
// from the ledger conversation log for that project.
export default function ChatPage() {
  return <ChatSurface mode="supervisor" />;
}
