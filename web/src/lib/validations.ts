import { z } from "zod";

export const contactSchema = z.object({
  name: z.string().trim().min(2, "Enter your name.").max(120),
  email: z.email("Enter a valid email address."),
  company: z.string().trim().max(160).optional(),
  message: z.string().trim().min(10, "Tell us what you'd hand over first.").max(4000),
  /** Honeypot: real users never see or fill this field. */
  website: z.string().optional(),
});

export type ContactInput = z.infer<typeof contactSchema>;
