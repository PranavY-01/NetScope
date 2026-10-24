import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  window.location.hash = "";
  try {
    window.localStorage.clear();
  } catch {
    // storage unavailable in this environment
  }
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.classList.remove("theme-switching");
});
