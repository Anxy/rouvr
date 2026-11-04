// Shell greeting (dashboard package): pure helpers over the real session
// username and the client clock. No fetches, no invented identity — the
// name comes from the login reply stored with the session (see
// sessionUsername in lib/api/workspace.ts) and only renders while the
// session probe says authenticated; the date is the real current date.

export type Daypart = "Good morning" | "Good afternoon" | "Good evening";

export function greetingForHour(hour: number): Daypart {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

// "Tuesday, November 4" — rendered uppercased as the eyebrow, Figma style.
export function formatShellDate(now: Date): string {
  return now.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

export interface ShellGreeting {
  eyebrow: string;
  title: string;
}

// Title carries the daypart plus the real session user's name when known;
// without a name (anonymous session) the daypart stands alone — never a
// placeholder identity.
export function shellGreeting(username: string | null, now: Date): ShellGreeting {
  const daypart = greetingForHour(now.getHours());
  const name = (username ?? "").trim();
  return {
    eyebrow: formatShellDate(now),
    title: name ? `${daypart}, ${name}` : daypart,
  };
}
