import nodemailer from "nodemailer";
import type { ContactInput } from "@/lib/validations";

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`Missing ${name}`);
  }
  return value;
}

/** Delivers a contact-form message to the Rouvr inbox over Hostinger SMTP. */
export async function sendContactEmail(input: Omit<ContactInput, "website">) {
  const user = requiredEnv("SMTP_USER");
  const port = Number(process.env.SMTP_PORT ?? "465");

  const transporter = nodemailer.createTransport({
    host: requiredEnv("SMTP_HOST"),
    port,
    secure: port === 465,
    auth: {
      user,
      pass: requiredEnv("SMTP_PASS"),
    },
    connectionTimeout: 10_000,
    greetingTimeout: 10_000,
    socketTimeout: 15_000,
  });

  const company = input.company?.trim();
  const subjectName = input.name.replace(/[\r\n]+/g, " ").slice(0, 80);

  await transporter.sendMail({
    from: `Rouvr website <${user}>`,
    to: process.env.CONTACT_TO?.trim() || user,
    replyTo: input.email,
    subject: `New message from ${subjectName}`,
    text: [
      `Name: ${input.name}`,
      `Email: ${input.email}`,
      company ? `Role or organisation: ${company}` : null,
      "",
      input.message,
    ]
      .filter((line) => line !== null)
      .join("\n"),
  });
}
