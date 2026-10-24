// A small, consistent stroke icon set (24×24 grid, 1.75 stroke, currentColor), drawn for NetScope.
// Icons are decorative: they are hidden from assistive technology, and every use sits next to text
// or inside a control that has its own accessible name.

const PATHS = {
  dashboard: (
    <>
      <rect x="3.5" y="3.5" width="7" height="8" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="5" rx="1.5" />
      <rect x="13.5" y="11.5" width="7" height="9" rx="1.5" />
      <rect x="3.5" y="14.5" width="7" height="6" rx="1.5" />
    </>
  ),
  radar: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 12 18 6" />
      <circle cx="12" cy="12" r="0.9" fill="currentColor" />
    </>
  ),
  history: (
    <>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.5-6" />
      <path d="M3.5 4v4h4" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
    </>
  ),
  moon: <path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10Z" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  download: (
    <>
      <path d="M12 4v11" />
      <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
      <path d="M5 19.5h14" />
    </>
  ),
  shield: <path d="M12 3 19 6v5.5c0 4.4-2.9 7.6-7 9-4.1-1.4-7-4.6-7-9V6l7-3Z" />,
  shieldCheck: (
    <>
      <path d="M12 3 19 6v5.5c0 4.4-2.9 7.6-7 9-4.1-1.4-7-4.6-7-9V6l7-3Z" />
      <path d="m9 12 2.2 2.2L15.5 10" />
    </>
  ),
  network: (
    <>
      <circle cx="12" cy="5" r="2" />
      <circle cx="5" cy="18.5" r="2" />
      <circle cx="19" cy="18.5" r="2" />
      <path d="M12 7v4.5M12 11.5 6.3 16.8M12 11.5l5.7 5.3" />
    </>
  ),
  server: (
    <>
      <rect x="3.5" y="4" width="17" height="6.5" rx="1.5" />
      <rect x="3.5" y="13.5" width="17" height="6.5" rx="1.5" />
      <path d="M7 7.25h.01M7 16.75h.01M11 7.25h6M11 16.75h6" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="5.5" rx="7.5" ry="2.5" />
      <path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13" />
      <path d="M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5" />
    </>
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  checkCircle: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m8.5 12.2 2.4 2.4 4.6-4.8" />
    </>
  ),
  alert: (
    <>
      <path d="M12 3.5 21 19.5H3L12 3.5Z" />
      <path d="M12 10v4M12 16.8h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5M12 7.8h.01" />
    </>
  ),
  xCircle: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m9 9 6 6M15 9l-6 6" />
    </>
  ),
  ban: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m6 6 12 12" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  activity: <path d="M3 12h4l3-7 4 14 3-7h4" />,
  stop: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <rect x="9" y="9" width="6" height="6" rx="1" />
    </>
  ),
  trash: (
    <>
      <path d="M4.5 7h15" />
      <path d="M9.5 7V4.5h5V7" />
      <path d="M6.5 7l1 13h9l1-13" />
    </>
  ),
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronLeft: <path d="m15 6-6 6 6 6" />,
  chevronRight: <path d="m9 6 6 6-6 6" />,
  terminal: (
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="2" />
      <path d="m7 9.5 3 2.5-3 2.5M12.5 15h4.5" />
    </>
  ),
  sliders: <path d="M5 4v6M5 14v6M12 4v10M12 18v2M19 4v2M19 10v10M3 12h4M10 16h4M17 8h4" />,
  plus: <path d="M12 5v14M5 12h14" />,
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className = "" }: { name: IconName; className?: string }) {
  return (
    <svg
      className={`icon ${className}`.trim()}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
