import { redirect } from "next/navigation";

/** Planner merged into Supervisor — keep URL for bookmarks. */
export default function PlannerPage() {
  redirect("/workspace/chat");
}
