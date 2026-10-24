import { useEffect, useState } from "react";

// Hash routing keeps the app a set of static files the API server can serve without rewrites.
export type Route =
  | { page: "dashboard" }
  | { page: "new" }
  | { page: "history" }
  | { page: "scan"; id: string }
  | { page: "not-found" };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, "") || "/";
  if (path === "/") return { page: "dashboard" };
  if (path === "/new") return { page: "new" };
  if (path === "/scans") return { page: "history" };
  const match = /^\/scans\/([0-9a-f]{32})$/.exec(path);
  if (match?.[1]) return { page: "scan", id: match[1] };
  return { page: "not-found" };
}

export function href(route: Route): string {
  switch (route.page) {
    case "dashboard":
      return "#/";
    case "new":
      return "#/new";
    case "history":
      return "#/scans";
    case "scan":
      return `#/scans/${route.id}`;
    default:
      return "#/";
  }
}

export function navigate(route: Route): void {
  window.location.hash = href(route);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}
