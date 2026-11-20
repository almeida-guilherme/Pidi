import { useEffect, useRef, useState, type PointerEvent } from "react";
import { Link } from "react-router-dom";
import { api, type Costs, type ProjectCost } from "../api";
import { dateTime } from "../format";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI", anthropic: "Claude", gemini: "Gemini", ollama: "Ollama", server: "This site's key",
};
const KIND_LABELS: Record<string, string> = {
  project: "Processing recordings", redo: "Redos", assess: "Assessments", template: "Reading templates",
};

/** Dollars as people read a bill: cents ("precise": a tenth of a cent under 10 cents), and "under $0.01" rather
than $0.00 for something that cost a little. */
export function usd(value: number, precise = false): string {
  if (value > 0 && value < 0.01) return "under $0.01";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2,
    maximumFractionDigits: precise && value < 0.1 ? 3 : 2 }).format(value);
}

function monthLabel(month: string, long = false): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleString("en-US", { month: long ? "long" : "short", year: long ? "numeric" : undefined, timeZone: "UTC" });
}

/** What the person's AI provider has cost them, estimated from tokens and list prices (web/usage.py). */
export default function CostsPage() {
  const [costs, setCosts] = useState<Costs | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.costs().then(setCosts, (e) => setError(e.message));
  }, []);

  if (error) return <main className="page wide"><p className="error">{error}</p></main>;
  if (!costs) return <main className="page wide"><p className="muted">Loading…</p></main>;
  const change = costs.this_month - costs.last_month;

  return (
    <main className="page wide costs-page">
      <div className="page-head">
        <h1>Dashboard</h1>
        <p>
          Your AI costs: what Pidi has spent on your AI provider, estimated from the tokens each action used and the provider's list
          prices. Your provider's billing page has the exact figures.
        </p>
      </div>

      <section className="card" aria-labelledby="sec-month">
        <div className="costs-hero">
          <div>
            <span id="sec-month" className="roi-hero-label">This month</span>
            <span className="roi-hero-value">{usd(costs.this_month)}</span>
            <span className="subtle small">
              {costs.last_month > 0
                ? `${change >= 0 ? "Up" : "Down"} ${usd(Math.abs(change))} on last month (${usd(costs.last_month)})`
                : "Nothing spent last month"}
            </span>
          </div>
          <dl className="costs-facts">
            <div><dt>All time</dt><dd>{usd(costs.total)}</dd></div>
            <div><dt>Per minute of video</dt><dd>{costs.per_minute !== null ? usd(costs.per_minute, true) : "–"}</dd></div>
            <div><dt>Projects</dt><dd>{costs.projects.length}</dd></div>
          </dl>
        </div>
        <MonthlyChart months={costs.months} />
        {costs.unpriced.length > 0 && (
          <p className="notice small">
            Not counted: {costs.unpriced.join(", ")}. Pidi has no price for {costs.unpriced.length === 1 ? "this model" : "these models"},
            so {costs.unpriced.length === 1 ? "its" : "their"} cost is missing from these figures.
          </p>
        )}
      </section>

      <section className="card" aria-labelledby="sec-where">
        <div className="card-head">
          <h2 id="sec-where">Where the money goes</h2>
          <p className="subtle">All time, by what Pidi did and by model.</p>
        </div>
        <div className="costs-split">
          <Bars title="By action" rows={Object.entries(costs.by_kind).filter(([, v]) => v > 0)
            .map(([k, v]) => ({ label: KIND_LABELS[k] ?? k, value: v }))} />
          <Bars title="By model" rows={Object.entries(costs.by_model).filter(([, v]) => v > 0)
            .map(([k, v]) => ({ label: k === "whisper-1" ? "Whisper (narration)" : k, value: v }))} />
        </div>
      </section>

      <section className="card" aria-labelledby="sec-projects">
        <div className="card-head">
          <h2 id="sec-projects">Projects</h2>
          <p className="subtle">Each project's cost, its redos and assessment included. Deleted projects still count in the totals above.</p>
        </div>
        <ProjectsTable projects={costs.projects} />
      </section>
    </main>
  );
}

/** Spending by month, the last twelve: one bar each; hovering shows the providers it went to. */
function MonthlyChart({ months }: { months: Costs["months"] }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const observer = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  const totals = months.map((m) => Object.values(m.by_provider).reduce((a, b) => a + b, 0));
  const max = Math.max(...totals, 0.01);
  const step = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 500, 1000]
    .find((s) => s * 4 >= max) ?? 1000;
  const top = step * Math.ceil(max / step);
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const height = 200, left = 56, right = 8, topPad = 10, bottom = 26;
  const plotW = Math.max(width - left - right, 10), plotH = height - topPad - bottom;
  const slot = plotW / months.length, barW = Math.min(36, slot * 0.6);
  const y = (v: number) => topPad + plotH - (v / top) * plotH;

  function move(e: PointerEvent<SVGSVGElement>) {
    const i = Math.floor((e.clientX - e.currentTarget.getBoundingClientRect().left - left) / slot);
    setHover(i >= 0 && i < months.length ? i : null);
  }
  const shown = hover === null ? null : months[hover];

  return (
    <figure className="roi-chart costs-chart">
      <figcaption>
        <strong>By month</strong>
        <span className="subtle small">The last twelve months, estimated</span>
      </figcaption>
      <div ref={box} className="roi-plot">
        {width > 0 && (
          <svg width={width} height={height} role="img" onPointerMove={move} onPointerLeave={() => setHover(null)}
               aria-label={`Spending by month: ${months.map((m, i) => `${monthLabel(m.month, true)} ${usd(totals[i])}`).join(", ")}.`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={left} x2={left + plotW} y1={y(t)} y2={y(t)} className={t === 0 ? "axis-zero" : "grid"} />
                <text x={left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tick">{usd(t)}</text>
              </g>
            ))}
            {months.map((m, i) => {
              const x = left + i * slot + (slot - barW) / 2, h = y(0) - y(totals[i]);
              return (
                <g key={m.month}>
                  {totals[i] > 0 && (
                    <path className={`cost-bar${hover === i ? " on" : ""}`}
                          d={`M${x},${y(0)}V${y(0) - h + Math.min(4, h)}q0,-${Math.min(4, h)} ${Math.min(4, h)},-${Math.min(4, h)}H${x + barW - Math.min(4, h)}q${Math.min(4, h)},0 ${Math.min(4, h)},${Math.min(4, h)}V${y(0)}Z`} />
                  )}
                  {(months.length - 1 - i) % (width < 520 ? 2 : 1) === 0 && ( // every other month on a phone, the latest kept
                    <text x={x + barW / 2} y={height - 8} textAnchor="middle" className="tick">{monthLabel(m.month)}</text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
        {shown && hover !== null && (
          <div className="roi-tooltip" style={{ left: Math.min(left + hover * slot + slot / 2 + 8, width - 170), top: 0 }}>
            <span className="subtle small">{monthLabel(shown.month, true)}</span>
            <strong>{usd(totals[hover])}</strong>
            {Object.entries(shown.by_provider).filter(([, v]) => v > 0).map(([p, v]) => (
              <span key={p} className="small">{PROVIDER_LABELS[p] ?? p}: {usd(v)}</span>
            ))}
          </div>
        )}
      </div>
      <details className="roi-table">
        <summary>Figures by month</summary>
        <table>
          <thead><tr><th scope="col">Month</th><th scope="col">Spent</th><th scope="col">Providers</th></tr></thead>
          <tbody>
            {months.map((m, i) => (
              <tr key={m.month}>
                <td>{monthLabel(m.month, true)}</td><td>{usd(totals[i])}</td>
                <td>{Object.entries(m.by_provider).filter(([, v]) => v > 0)
                  .map(([p, v]) => `${PROVIDER_LABELS[p] ?? p} ${usd(v)}`).join(", ") || "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** A short ranking: one bar per item, longest first, the value printed beside it. */
function Bars({ title, rows }: { title: string; rows: { label: string; value: number }[] }) {
  const sorted = [...rows].sort((a, b) => b.value - a.value);
  const max = Math.max(...sorted.map((r) => r.value), 0.0001);
  const total = sorted.reduce((a, r) => a + r.value, 0);
  return (
    <figure className="roi-chart">
      <figcaption><strong>{title}</strong></figcaption>
      {sorted.length === 0 ? <p className="subtle small">Nothing spent yet.</p> : (
        <div className="roi-bars">
          {sorted.map((r) => (
            <div key={r.label} className="roi-bar-row" title={`${r.label}: ${usd(r.value, true)}`}>
              <span className="roi-bar-label">{r.label}
                <span className="subtle small"> · {Math.round((r.value / (total || 1)) * 100)}%</span></span>
              <span className="roi-bar-track">
                <span className="roi-bar" style={{ width: `${(r.value / max) * 100}%` }} />
                <span className="roi-bar-value">{usd(r.value)}</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </figure>
  );
}

type Sort = "date" | "cost" | "per_minute";

function ProjectsTable({ projects }: { projects: ProjectCost[] }) {
  const [sort, setSort] = useState<Sort>("date");
  if (projects.length === 0) return <p className="subtle">No projects yet.</p>;
  const sorted = [...projects].sort((a, b) =>
    sort === "cost" ? b.total - a.total
      : sort === "per_minute" ? (b.per_minute ?? -1) - (a.per_minute ?? -1)
      : b.created_at.localeCompare(a.created_at));
  const header = (key: Sort, label: string) => (
    <th scope="col" aria-sort={sort === key ? "descending" : "none"}>
      <button type="button" className={`sort-btn${sort === key ? " on" : ""}`} onClick={() => setSort(key)}>
        {label}{sort === key ? " ↓" : ""}
      </button>
    </th>
  );
  return (
    <div className="table-scroll">
      <table className="costs-table">
        <thead>
          <tr>
            <th scope="col">Project</th>
            {header("date", "Created")}
            <th scope="col">Video</th>
            <th scope="col">Provider and model</th>
            {header("cost", "Cost")}
            {header("per_minute", "Per minute")}
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => {
            const extra = (p.by_kind.redo ?? 0) + (p.by_kind.assess ?? 0);
            return (
              <tr key={p.run_id}>
                <td><Link to={`/runs/${p.run_id}`}>{p.name}</Link></td>
                <td className="subtle">{dateTime(p.created_at)}</td>
                <td>{p.minutes !== null ? `${p.minutes} min` : "–"}</td>
                <td className="small">
                  {p.providers.map((x) => PROVIDER_LABELS[x] ?? x).join(", ")}
                  <span className="subtle"> · {p.models.filter((m) => m !== "whisper-1").join(", ") || "–"}</span>
                </td>
                <td className="num">
                  {usd(p.total)}
                  {extra > 0 && <span className="subtle small" style={{ display: "block" }}>incl. {usd(extra)} redos and assessment</span>}
                </td>
                <td className="num">{p.per_minute !== null ? usd(p.per_minute, true) : "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
