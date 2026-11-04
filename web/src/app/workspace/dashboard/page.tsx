import { redirect } from "next/navigation";

// The dashboard page was removed from the product (frozen scope cut);
// the route redirects to the board so old links keep working.
export default function DashboardPage() {
  redirect("/workspace");
}
