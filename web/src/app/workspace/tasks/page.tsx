import { Planned } from "@/components/workspace/Planned";

// Tasks index until a dedicated list ships: an honest Planned shell with a
// working way to reach every task today. Task detail (/workspace/tasks/[id])
// is unaffected.
export default function TasksPage() {
  return (
    <Planned
      title="Tasks"
      blurb="A dedicated task list will live here."
      actionHref="/workspace"
      actionLabel="View tasks on the Board"
    />
  );
}
