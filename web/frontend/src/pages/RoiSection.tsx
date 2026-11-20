import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Roi, type Run } from "../api";
import RoiDashboard from "./RoiDashboard";
import { INDICATORS, VERDICTS, formatters, indicatorLabel, indicatorValue, isNegative, minutesText } from "../roi";

/** The project's ROI, read-only: worked out from the ROI template chosen when the project was created and from
what the recording shows. Projects without an ROI template have none. */
export default function RoiSection({ run, onChange }: { run: Run; onChange: (run: Run) => void }) {
  const roi = run.roi!;
  // Opened at /runs/<id>#roi: straight to this section.
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (window.location.hash === "#roi") ref.current?.scrollIntoView({ block: "start" });
  }, []);
  const { inputs, result } = roi;
  if (!inputs.template_name) return null;
  const { money } = formatters(inputs.currency);

  const sources = { given: "given", estimated: "estimated from the recording", recorded: "as recorded, narration included" };
  const time = result.minutes_per_run === null ? "Not known yet"
    : `${minutesText(result.minutes_per_run)} (${sources[result.minutes_source!]})`;
  const pending = run.status !== "done" ? "The figures from the recording are filled in when the project is ready."
    : result.runs_per_month === null ? "The recording doesn't say how often the process runs: add the runs per month below to work out the savings."
    : null;

  return (
    <section className="card" id="roi" ref={ref} aria-labelledby="sec-roi">
      <div className="card-head">
        <h2 id="sec-roi">Return on investment (ROI)</h2>
        <p className="subtle">
          With the ROI template “{inputs.template_name}”. It's in the Word document too, in its own section.
        </p>
      </div>
      <RoiDashboard inputs={inputs} result={result} />
      {inputs.indicators.length > 0 && (
        <dl className="stats roi-results">
          {INDICATORS.filter((x) => inputs.indicators.includes(x.key)).map((x) => (
            <div key={x.key}>
              <dt>{indicatorLabel(x.key, inputs)}</dt>
              <dd className={isNegative(x.key, result) ? "negative" : undefined}>{indicatorValue(x.key, result, inputs)}</dd>
            </div>
          ))}
        </dl>
      )}
      {pending && <p className="subtle small">{pending}</p>}
      <div>
        <p className="field-title">Figures used</p>
        <dl className="facts roi-facts">
          <dt>Runs per month</dt><dd><RunsPerMonth run={run} onChange={onChange} /></dd>
          <dt>Time per run</dt><dd>{time}</dd>
          <dt>Cost per hour</dt><dd>{money(inputs.hourly_cost)}</dd>
          <dt>Cost to build</dt><dd>{money(inputs.build_cost ?? 0)}</dd>
          <dt>Cost to run, per month</dt><dd>{money(inputs.monthly_cost ?? 0)}</dd>
        </dl>
      </div>
    </section>
  );
}

/** The runs per month, which the recording often doesn't say: the author gives them, or clears them to go back to
the recording's. Saving writes the Word document again, with no model call. */
function RunsPerMonth({ run, onChange }: { run: Run; onChange: (run: Run) => void }) {
  const { inputs, result } = run.roi!;
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const said = run.roi!.assessment?.runs_per_month ?? null;

  function start() {
    setText(inputs.runs_per_month === null ? "" : String(inputs.runs_per_month));
    setError(null);
    setEditing(true);
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    const value = text.trim() === "" ? null : Number(text.replace(",", "."));
    if (value !== null && !(value > 0 && value <= 1_000_000)) return setError("Type a number above 0, or leave it empty.");
    setSaving(true);
    setError(null);
    try {
      onChange(await api.saveRoi(run.id, { ...inputs, runs_per_month: value }));
      setEditing(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    const shown = result.runs_per_month === null ? "Not said in the recording"
      : `${formatters(inputs.currency).number(result.runs_per_month, Number.isInteger(result.runs_per_month) ? 0 : 1)} (${result.runs_source === "narration" ? "as said in the recording" : "given"})`;
    return (
      <span className="runs-value">
        {shown}
        {run.status === "done" && (
          <button type="button" className="link-btn" onClick={start}>{result.runs_per_month === null ? "Add" : "Change"}</button>
        )}
      </span>
    );
  }
  return (
    <form className="runs-form" onSubmit={save}>
      <input type="number" inputMode="decimal" min={0} step="any" autoFocus aria-label="Runs per month"
             placeholder={said === null ? "e.g. 120" : String(said)} value={text}
             onChange={(e) => setText(e.target.value)} disabled={saving} />
      <button className="btn btn-dark btn-sm" disabled={saving}>{saving ? "Saving…" : "Save"}</button>
      <button type="button" className="btn btn-outline btn-sm" disabled={saving} onClick={() => setEditing(false)}>Cancel</button>
      <span className="subtle small runs-hint">
        {said === null ? "How many times a month the process is done." : `Leave it empty to use the recording's ${said}.`}
        {" "}The Word document is updated too.
      </span>
      {error && <span className="error small" role="alert">{error}</span>}
    </form>
  );
}

const VERDICT_TEXT = {
  good: "Worth automating as it is.",
  partial: "Part of it can be automated, or only after some changes.",
  poor: "Not a good fit for automation as it is.",
};
const LISTS = [
  { key: "strengths", title: "In favour", icon: "✓", tone: "good" },
  { key: "blockers", title: "Against", icon: "✕", tone: "poor" },
  { key: "recommendations", title: "Recommendations", icon: "→", tone: "neutral" },
] as const;

/** A project made before the assessment existed: assessed on request, free (one model call on the saved results). */
export function AssessPrompt({ run, onChange }: { run: Run; onChange: (run: Run) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.assess(run.id));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }
  return (
    <section className="card" aria-labelledby="sec-viability">
      <div className="card-head">
        <h2 id="sec-viability">Automation viability</h2>
        <p className="subtle">
          This project was made before Pidi assessed whether a process is worth automating. Pidi can assess it now
          from what it already found in the recording, without processing the video again. It's free and takes about
          half a minute; the Word document gets the assessment too.
        </p>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions" style={{ justifyContent: "flex-start" }}>
        <button type="button" className="btn btn-dark" disabled={busy} onClick={go}>
          {busy ? "Assessing…" : "Assess this project"}
        </button>
      </div>
      {busy && <p className="subtle small" aria-live="polite">Assessing the process… this page updates when it's done.</p>}
    </section>
  );
}

/** Whether the process is worth automating, as the AI assessed it from the recording. */
export function ViabilitySection({ roi }: { roi: Roi }) {
  const a = roi.assessment;
  if (!a) return null;
  const verdict = VERDICTS[a.verdict];
  return (
    <section className="card" aria-labelledby="sec-viability">
      <h2 id="sec-viability">Automation viability</h2>
      <div className={`verdict-banner verdict-banner-${verdict.tone}`}>
        <span className="verdict-mark" aria-hidden="true">{a.verdict === "good" ? "✓" : a.verdict === "partial" ? "~" : "✕"}</span>
        <div>
          <p className="verdict-title">{verdict.label}</p>
          <p className="verdict-lead">{VERDICT_TEXT[a.verdict]} {a.summary}</p>
        </div>
      </div>
      <div className="viability-cols">
        {LISTS.filter((l) => a[l.key].length > 0).map((l) => (
          <div key={l.key} className={`viability-col viability-col-${l.tone}`}>
            <h3><span className="viability-icon" aria-hidden="true">{l.icon}</span>{l.title}</h3>
            <ul>{a[l.key].map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        ))}
      </div>
      <p className="subtle small">Assessed by AI from the recording; review it before deciding. It's in the Word document too.</p>
    </section>
  );
}
