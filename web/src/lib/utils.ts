import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const SITE_URL = "https://rouvr.si";

/** Public inbox from the site content draft. Swap if the team confirms a different address. */
export const CONTACT_EMAIL = "hello@rouvr.si";
