// A stacked-bar distribution of real counts (scan statuses, result states). No chart library: the
// data is a handful of categories, and plain elements theme cleanly through CSS tokens.

export interface Segment {
  key: string;
  label: string;
  value: number;
  /** A CSS tone class, e.g. "tone-success"; colours come from the active theme. */
  tone: string;
}

export function Distribution({ title, segments, unit }: { title: string; segments: Segment[]; unit: string }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const visible = segments.filter((s) => s.value > 0);
  const summary = visible.length
    ? visible.map((s) => `${s.label} ${s.value}`).join(", ")
    : `no ${unit} yet`;

  return (
    <figure className="dist">
      <figcaption className="dist-head">
        <span className="dist-title">{title}</span>
        <span className="dist-total mono">
          {total} {unit}
        </span>
      </figcaption>
      <div className="dist-bar" role="img" aria-label={`${title}: ${summary}`}>
        {total === 0 ? (
          <span className="dist-empty" />
        ) : (
          visible.map((s) => {
            const percent = (s.value / total) * 100;
            return (
              <span
                key={s.key}
                className={`dist-seg ${s.tone}`}
                style={{ flexGrow: s.value }}
                data-tip={`${s.label}: ${s.value} (${percent < 1 ? "<1" : Math.round(percent)}%)`}
                tabIndex={0}
              />
            );
          })
        )}
      </div>
      <ul className="dist-legend">
        {segments.map((s) => (
          <li key={s.key} className={s.value === 0 ? "is-zero" : undefined}>
            <span className={`swatch ${s.tone}`} aria-hidden="true" />
            {s.label}
            <span className="mono">{s.value}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
