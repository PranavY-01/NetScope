import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { NewScan } from "./pages/NewScan";
import { ScanDetail } from "./pages/ScanDetail";
import { href, useRoute, type Route } from "./router";

const NAV: { label: string; route: Route; matches: Route["page"][] }[] = [
  { label: "Dashboard", route: { page: "dashboard" }, matches: ["dashboard"] },
  { label: "New scan", route: { page: "new" }, matches: ["new"] },
  { label: "History", route: { page: "history" }, matches: ["history", "scan"] },
];

export function App() {
  const route = useRoute();
  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href={href({ page: "dashboard" })}>
          NetScope
        </a>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <a key={item.label} href={href(item.route)} aria-current={item.matches.includes(route.page) ? "page" : undefined}>
              {item.label}
            </a>
          ))}
        </nav>
        <span className="topbar-note">Authorized networks only</span>
      </header>
      <main className="content">
        <Page route={route} />
      </main>
    </div>
  );
}

function Page({ route }: { route: Route }) {
  switch (route.page) {
    case "dashboard":
      return <Dashboard />;
    case "new":
      return <NewScan />;
    case "history":
      return <History />;
    case "scan":
      return <ScanDetail key={route.id} id={route.id} />;
    default:
      return (
        <section>
          <h1>Page not found</h1>
          <p>
            <a href={href({ page: "dashboard" })}>Go to the dashboard</a>
          </p>
        </section>
      );
  }
}
