import { Planned } from "@/components/workspace/Planned";

// Projects index until the projects tab ships: an honest Planned shell, not
// a dead nav button.
export default function ProjectsPage() {
  return (
    <Planned
      title="Projects"
      blurb="The tenant project list with open/blocked/done counts and last activity will live here."
      actionHref="/workspace"
      actionLabel="Back to Board"
    />
  );
}
