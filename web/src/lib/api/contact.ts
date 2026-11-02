/** Same-origin Next.js routes, sent through the shared client helper. */
import type { ContactInput } from "../validations";
import { ApiError, requestJson } from "./client";

export interface ContactResult {
  ok: boolean;
  error?: string;
}

/** POST /api/contact. Throws Error with the server message on failure. */
export async function submitContact(values: ContactInput): Promise<void> {
  try {
    const data = await requestJson<ContactResult>("/api/contact", {
      method: "POST",
      body: values,
    });
    if (!data.ok) throw new Error(data.error ?? "Something went wrong.");
  } catch (error) {
    if (error instanceof ApiError) throw new Error(error.detail || "Something went wrong.");
    throw error;
  }
}
