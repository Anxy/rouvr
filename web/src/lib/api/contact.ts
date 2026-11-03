import { requestJson } from "./client";
import type { ContactInput } from "../validations";

export interface ContactResult {
  ok: boolean;
  error?: string;
}

/** POST the contact form to the same-origin Next.js route. */
export function postContact(values: ContactInput): Promise<ContactResult> {
  return requestJson<ContactResult>("/api/contact", {
    method: "POST",
    body: JSON.stringify(values),
  });
}
