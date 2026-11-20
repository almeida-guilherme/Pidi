import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type RoiTemplate } from "../api";
import { PlusIcon } from "../components";
import { dateTime } from "../format";
import { formatters } from "../roi";

/** ROI templates: a company's assumptions, chosen for projects. */
export default function RoiPage() {
  const [templates, setTemplates] = useState<RoiTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.roiTemplates().then(setTemplates, (e) => setError(e.message));
  }, []);

  return (
    <main className="page wide">
      <div className="page-head">
        <h1>Return on Investment</h1>
        <p>
          Set up your company's ROI assumptions once as a template and choose it for your projects. Each project's
          page shows what automating its process would save and whether it's worth it.
        </p>
      </div>
      {error && <p className="error">{error}</p>}

      <section className="card" aria-labelledby="sec-roi-templates">
        <h2 id="sec-roi-templates">ROI templates</h2>
        <div>
          <Link to="/roi/templates/new" className="btn btn-dark"><PlusIcon /> New ROI template</Link>
        </div>
        {templates === null ? <p className="muted">Loading…</p> : templates.length === 0 ? (
          <p className="subtle">
            None yet. An ROI template holds your cost per hour, the cost to build and run an automation and
            the indicators you report, so each project only needs its own runs per month and time per run.
          </p>
        ) : (
          <ul className="roi-template-list">
            {templates.map((t) => {
              const { money } = formatters(t.assumptions.currency);
              return (
                <li key={t.id}>
                  <Link to={`/roi/templates/${t.id}`}>{t.name}</Link>
                  <span className="subtle small">
                    {money(t.assumptions.hourly_cost)}/h · build {money(t.assumptions.build_cost)} ·{" "}
                    {t.assumptions.indicators.length} indicators · changed {dateTime(t.updated_at)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

    </main>
  );
}
