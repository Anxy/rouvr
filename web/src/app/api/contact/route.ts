import { NextResponse } from "next/server";
import { sendContactEmail } from "@/lib/mail";
import { contactSchema } from "@/lib/validations";

export const runtime = "nodejs";

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: "Invalid request." }, { status: 400 });
  }

  const parsed = contactSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { ok: false, error: parsed.error.issues[0]?.message ?? "Invalid input." },
      { status: 422 },
    );
  }

  if (parsed.data.website) {
    return NextResponse.json({ ok: true });
  }

  const { name, email, company, message } = parsed.data;

  try {
    await sendContactEmail({ name, email, company, message });
  } catch (error) {
    console.error("Contact email failed:", error instanceof Error ? error.message : error);
    return NextResponse.json(
      { ok: false, error: "We couldn't send that just now. Please try again." },
      { status: 500 },
    );
  }

  return NextResponse.json({ ok: true });
}
