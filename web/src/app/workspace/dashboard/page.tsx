import { Planned } from "@/components/workspace/Planned";

// Dashboard landing until the dashboard page ships: an honest Planned
// shell, not a dead nav button.
export default function DashboardPage() {
  return (
    <Planned
      title="Dashboard"
      blurb="KPIs, delivery velocity, the AI team strip, and the active-tasks table will live here."
      actionHref="/workspace"
      actionLabel="Back to Board"
    />
  );
}
