import type { ContactInput } from "@/lib/validations";

export interface ContactResult {
  ok: boolean;
  error?: string;
}

/** Submit the site contact form (site-local route, kept in the api lib with the generated client). */
export async function submitContact(values: ContactInput): Promise<void> {
  const res = await fetch("/api/contact", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(values),
  });
  const data = (await res.json()) as ContactResult;
  if (!res.ok || !data.ok) throw new Error(data.error ?? "Something went wrong.");
}
