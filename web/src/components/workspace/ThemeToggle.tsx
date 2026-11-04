"use client";

// Workspace sun/moon theme toggle. Same storage contract as the root
// themeBoot script and the marketing toggle: localStorage key "theme",
// values "light"/"dark", mirrored onto the <html> light/dark class (which
// drives the html.light token overrides) plus data-theme. Storage is
// written before the class flip so the root MutationObserver converges on
// the new value. The sun is the Figma icon verbatim; the moon is drawn in
// the same stroke style (24-grid, 1.8 stroke, round caps). Visibility is
// pure CSS via .theme-icon-sun/.theme-icon-moon, so the server render
// matches either theme with no hydration flash.

const STORAGE_KEY = "theme";

function applyTheme(theme: "light" | "dark") {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* Storage can be blocked; the class still switches for this visit. */
  }
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(theme);
  root.style.colorScheme = theme;
  root.setAttribute("data-theme", theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#F3F6FA" : "#040B13");
}

function SunIcon() {
  return (
    <svg
      aria-hidden
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="theme-icon-sun"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      aria-hidden
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="theme-icon-moon"
    >
      <path d="M20 13.5A8 8 0 0 1 10.5 4 6.5 6.5 0 1 0 20 13.5z" />
    </svg>
  );
}

export function ThemeToggle() {
  return (
    <button
      type="button"
      aria-label="Toggle color theme"
      data-theme-toggle
      onClick={() => {
        const next = document.documentElement.classList.contains("light") ? "dark" : "light";
        applyTheme(next);
      }}
      className="grid h-[34px] w-[34px] shrink-0 place-items-center rounded-lg text-dim transition-colors hover:bg-panel2 hover:text-fog"
    >
      <SunIcon />
      <MoonIcon />
    </button>
  );
}
