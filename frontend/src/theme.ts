import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

// Must match public/theme-init.js, which applies the theme before React loads.
export const THEME_KEY = "netscope-theme";
const SWITCH_MS = 300;

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null; // e.g. storage disabled: the theme still works, it just isn't remembered
  }
}

export function storedTheme(): Theme | null {
  const value = storage()?.getItem(THEME_KEY);
  return value === "light" || value === "dark" ? value : null;
}

export function systemTheme(): Theme {
  if (typeof window.matchMedia !== "function") return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function initialTheme(): Theme {
  const applied = document.documentElement.dataset.theme;
  if (applied === "light" || applied === "dark") return applied;
  return storedTheme() ?? systemTheme();
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

let switchTimer: ReturnType<typeof setTimeout> | undefined;

/** Set the theme on <html>. With `animate`, colours cross-fade briefly; nothing else is transitioned. */
export function applyTheme(theme: Theme, animate = false): void {
  const root = document.documentElement;
  if (animate && !prefersReducedMotion()) {
    root.classList.add("theme-switching");
    clearTimeout(switchTimer);
    switchTimer = setTimeout(() => root.classList.remove("theme-switching"), SWITCH_MS);
  }
  root.dataset.theme = theme;
}

/** Current theme and a toggle. An explicit choice is saved; otherwise the system preference is followed. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const follow = () => {
      if (storedTheme() === null) setTheme(query.matches ? "light" : "dark");
    };
    query.addEventListener?.("change", follow);
    return () => query.removeEventListener?.("change", follow);
  }, []);

  const toggle = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next, true);
    try {
      storage()?.setItem(THEME_KEY, next);
    } catch {
      // quota or privacy mode: keep the switch, just don't remember it
    }
    setTheme(next);
  }, [theme]);

  return [theme, toggle];
}
