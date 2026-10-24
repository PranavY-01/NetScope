import { Icon, type IconName } from "./components/Icon";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { NewScan } from "./pages/NewScan";
import { ScanDetail } from "./pages/ScanDetail";
import { href, useRoute, type Route } from "./router";
import { useTheme } from "./theme";

const NAV: { label: string; icon: IconName; route: Route; matches: Route["page"][] }[] = [
  { label: "Dashboard", icon: "dashboard", route: { page: "dashboard" }, matches: ["dashboard"] },
  { label: "New scan", icon: "radar", route: { page: "new" }, matches: ["new"] },
  { label: "History", icon: "history", route: { page: "history" }, matches: ["history", "scan"] },
];

export function App() {
  const route = useRoute();
  const [theme, toggleTheme] = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const pageKey = route.page === "scan" ? `scan-${route.id}` : route.page;

  return (
    <div className="app">
      {/* Static decorative radar backdrop: pure CSS, no animation, hidden from assistive technology. */}
      <div className="backdrop" aria-hidden="true">
        <span className="radar radar-main">
          <span className="radar-ticks" />
          <span className="radar-majors" />
        </span>
        <span className="radar radar-minor">
          <span className="radar-ticks" />
        </span>
      </div>
      <header className="topbar">
        <a className="brand" href={href({ page: "dashboard" })} aria-label="NetScope dashboard">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="radar" />
          </span>
          <span className="brand-name">NetScope</span>
          <span className="brand-tag mono">ops console</span>
        </a>
        <nav aria-label="Main" className="nav">
          {NAV.map((item) => (
            <a key={item.label} href={href(item.route)} aria-current={item.matches.includes(route.page) ? "page" : undefined}>
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </a>
          ))}
        </nav>
        <div className="topbar-end">
          <span className="notice">
            <Icon name="shieldCheck" />
            <span className="notice-text">Authorized networks only</span>
          </span>
          <button
            type="button"
            className="theme-toggle"
            onClick={toggleTheme}
            aria-label={`Switch to ${next} theme`}
            title={`Switch to ${next} theme`}
          >
            <Icon name={theme === "dark" ? "sun" : "moon"} />
          </button>
        </div>
      </header>
      <main className="content">
        <div className="page" key={pageKey}>
          <Page route={route} />
        </div>
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
      return <ScanDetail id={route.id} />;
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
