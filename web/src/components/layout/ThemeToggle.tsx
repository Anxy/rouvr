"use client";

import { Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";

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

export function ThemeToggle({ className }: { className?: string }) {
  return (
    <button
      type="button"
      aria-label="Toggle color theme"
      onClick={() => {
        const next = document.documentElement.classList.contains("light") ? "dark" : "light";
        applyTheme(next);
      }}
      className={cn(
        "grid size-10 place-items-center rounded-full border border-white/20 text-white transition-colors duration-300 hover:border-brand/60 hover:bg-white/10",
        className,
      )}
    >
      <Sun className="theme-icon-sun size-4" aria-hidden />
      <Moon className="theme-icon-moon size-4" aria-hidden />
    </button>
  );
}
