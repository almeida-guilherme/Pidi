import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type ProcessModel, type Run, type Step, type StepIssue, type StepNote } from "../api";

/** What can be wrong with a step, in the order offered; `required`: can't be acted on without a note. */
export const ISSUES: { issue: StepIssue; label: string; placeholder: string; required?: boolean }[] = [
  { issue: "wording", label: "Wrong wording", placeholder: "How should it read? (optional)" },
  { issue: "missing_after", label: "A step is missing after it", placeholder: "What's missing?", required: true },
  { issue: "split", label: "Should be two steps", placeholder: "Where does the second one start? (optional)" },
  { issue: "merge_next", label: "Same step as the next one", placeholder: "Anything to add? (optional)" },
  { issue: "screenshot", label: "Wrong screenshot", placeholder: "What should it show? (optional)" },
  { issue: "name", label: "Wrong name or system", placeholder: "The right name" },
  { issue: "other", label: "Something else", placeholder: "What's wrong?", required: true },
];

/** The author says what's wrong with the document: in general, and step by step, with each step's screenshot. */
export default function ReviewPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState<Run | null>(null);
  const [model, setModel] = useState<ProcessModel | null>(null);
  const [general, setGeneral] = useState("");
  const [notes, setNotes] = useState<StepNote[]>([]);
  const [locked, setLocked] = useState<string | null>(null); // why the notes can't be changed now
  const [changed, setChanged] = useState<{ version: number; steps: number[] } | null>(null); // by the last redo
  const [redosLeft, setRedosLeft] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.run(id), api.model(id), api.review(id)]).then(([r, m, review]) => {
      setRun(r);
      setModel(m);
      setGeneral(review.feedback.general);
      setNotes(review.feedback.steps);
      setLocked(review.status === "approved"
        ? "This project is approved. Undo the approval on the project's page to ask for changes."
        : review.status === "redoing" ? "The document is being done again with your notes; wait for it to finish." : null);
      const last = review.versions.at(-1);
      if (last && last.number === review.version) setChanged({ version: last.number, steps: last.changed });
      setRedosLeft(review.redos_left);
    }, (e: ApiError) => setError(e.status === 404 ? "This project doesn't exist." : e.message));
  }, [id]);

  async function save(redo = false) {
    const missing = notes.find((n) => ISSUES.find((i) => i.issue === n.issue)?.required && !n.note.trim());
    if (missing) {
      setError(`Step ${missing.number}: say what's ${missing.issue === "missing_after" ? "missing" : "wrong"}.`);
      document.getElementById(`step-${missing.number}`)?.scrollIntoView({ block: "center" });
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.saveFeedback(id, { general, steps: notes });
      if (redo) await api.redo(id);
      navigate(`/runs/${id}#review`);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  if (error && !model) {
    return <main className="page medium"><p className="error">{error}</p><Link to={`/runs/${id}`} className="back">← Back to the project</Link></main>;
  }
  if (!run || !model) return <main className="page medium"><p className="muted">Loading…</p></main>;
  const marked = new Set(notes.map((n) => n.number)).size;

  return (
    <main className="page medium review-page">
      <Link to={`/runs/${id}`} className="back">← Back to the project</Link>
      <div className="page-head">
        <h1>Review the document</h1>
        <p>
          Mark what's wrong in “{run.name || model.process_name}”: a step at a time, or in general. Then “Save and
          redo”: Pidi looks at those steps again in the recording and does the document again, changing only what you
          marked.
        </p>
      </div>

      {locked && <p className="notice">{locked}</p>}

      <section className="card" aria-labelledby="sec-general">
        <h2 id="sec-general">In general</h2>
        <div className="field">
          <label htmlFor="rv-general" className="hint">
            Anything that isn't about one step, e.g. “the approval part is missing” or “it's all one process”.
          </label>
          <textarea id="rv-general" rows={3} maxLength={3000} value={general} disabled={!!locked}
                    onChange={(e) => setGeneral(e.target.value)} placeholder="Optional" />
        </div>
      </section>

      <section className="card" aria-labelledby="sec-steps">
        <div className="card-head">
          <h2 id="sec-steps">Steps</h2>
          <p className="subtle">{model.as_is.length} steps, as in the document. Mark only the ones that are wrong.</p>
        </div>
        <ol className="steps review-steps">
          {model.as_is.map((step) => (
            <ReviewStep key={step.number} step={step} disabled={!!locked}
                        changedIn={changed?.steps.includes(step.number) ? changed.version : null}
                        notes={notes.filter((n) => n.number === step.number)}
                        onChange={(change) => setNotes((all) => {
                          const mine = change(all.filter((n) => n.number === step.number));
                          return [...all.filter((n) => n.number !== step.number), ...mine].sort((a, b) => a.number - b.number);
                        })} />
          ))}
        </ol>
      </section>

      <div className="review-bar">
        <span className="subtle small" aria-live="polite">
          {marked === 0 && !general.trim() ? "Nothing marked yet"
            : `${marked} step${marked === 1 ? "" : "s"} marked${general.trim() ? ", and a general note" : ""}`}
        </span>
        {error && <span className="error small" role="alert">{error}</span>}
        <div className="review-bar-actions">
          <Link to={`/runs/${id}`} className="btn btn-outline">Cancel</Link>
          <button type="button" className="btn btn-outline" disabled={saving || !!locked} onClick={() => save()}>
            Save my notes
          </button>
          <button type="button" className="btn btn-dark" disabled={saving || !!locked || redosLeft === 0
                    || (notes.length === 0 && !general.trim())} onClick={() => save(true)}>
            {saving ? "Saving…" : "Save and redo"}
          </button>
        </div>
      </div>
    </main>
  );
}

/** One step, with what the author marked wrong in it. */
function ReviewStep({ step, notes, disabled, changedIn, onChange }: {
  step: Step; notes: StepNote[]; disabled: boolean; changedIn: number | null; onChange: (change: (mine: StepNote[]) => StepNote[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const shown = open || notes.length > 0;

  function toggle(issue: StepIssue) {
    onChange((mine) => mine.some((n) => n.issue === issue) ? mine.filter((n) => n.issue !== issue)
      : [...mine, { number: step.number, issue, note: "" }]);
  }
  function write(issue: StepIssue, note: string) {
    onChange((mine) => mine.map((n) => (n.issue === issue ? { ...n, note } : n)));
  }

  return (
    <li id={`step-${step.number}`} className={`step review-step${notes.length ? " marked" : ""}`}>
      <div className="step-text">
        <div className="step-meta">
          <span className="step-number">{step.number}</span>
          {step.application && <span className="chip">{step.application}</span>}
          {step.time && <span className="subtle small">at {step.time}</span>}
          {changedIn !== null && <span className="tag review-changed">Changed in version {changedIn}</span>}
          {notes.length > 0 && <span className="tag tag-remove">{notes.length} problem{notes.length === 1 ? "" : "s"}</span>}
        </div>
        <p>{step.instruction}</p>
        {step.decision && <p className="decision"><strong>Decision:</strong> {step.decision}</p>}
        {!shown && (
          <button type="button" className="btn btn-outline btn-sm review-open" disabled={disabled}
                  onClick={() => setOpen(true)}>Something's wrong</button>
        )}
      </div>
      {step.image_url ? (
        <a href={step.image_url} target="_blank" rel="noreferrer" className="step-shot">
          <img src={step.image_url} alt={`Screenshot of step ${step.number}`} loading="lazy" />
        </a>
      ) : (
        <div className="step-shot empty subtle small">No screenshot</div>
      )}
      {shown && (
          <div className="review-issues">
            <p className="field-title small">What's wrong with this step?</p>
            <div className="issue-chips" role="group" aria-label={`Problems with step ${step.number}`}>
              {ISSUES.map((i) => (
                <button key={i.issue} type="button" disabled={disabled}
                        className={`issue-chip${notes.some((n) => n.issue === i.issue) ? " on" : ""}`}
                        aria-pressed={notes.some((n) => n.issue === i.issue)} onClick={() => toggle(i.issue)}>
                  {i.label}
                </button>
              ))}
            </div>
            {ISSUES.filter((i) => notes.some((n) => n.issue === i.issue)).map((i) => (
              <label key={i.issue} className="issue-note">
                <span className="small">{i.label}</span>
                <input type="text" maxLength={600} disabled={disabled} placeholder={i.placeholder} required={i.required}
                       value={notes.find((n) => n.issue === i.issue)!.note} onChange={(e) => write(i.issue, e.target.value)} />
              </label>
            ))}
            {notes.length === 0 && (
              <button type="button" className="link-btn review-close" onClick={() => setOpen(false)}>Nothing's wrong</button>
            )}
          </div>
      )}
    </li>
  );
}
