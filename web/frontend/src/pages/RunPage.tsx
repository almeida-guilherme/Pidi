import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type ProcessModel, type Review, type Run, type SpecPreview } from "../api";
import { StatusPill } from "../components";
import { INPUT_KINDS, STAGE_LABELS, dateTime, duration } from "../format";
import { templateLabel, useSite } from "../site";
import RoiSection, { AssessPrompt, ViabilitySection } from "./RoiSection";
import { ISSUES } from "./ReviewPage";
import { usd } from "./CostsPage";
import { usePolling } from "../usePolling";

export default function RunPage() {
  const site = useSite();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState<Run | null>(null);
  const [model, setModel] = useState<ProcessModel | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.run(id).then(setRun, (e: ApiError) => setError(e.status === 404 ? "This project doesn't exist." : e.message));
  }, [id]);
  useEffect(load, [load]);
  const active = run?.status === "queued" || run?.status === "running";
  usePolling(load, 2000, active);

  useEffect(() => {
    if (run?.status === "done" && !model) api.model(id).then(setModel, (e) => setError(e.message));
  }, [run?.status, id, model]);

  async function retry() {
    setRun(await api.retry(id));
  }

  async function remove() {
    if (!confirm("Delete this project, its document, screenshots and the uploaded recording?")) return;
    try {
      await api.remove(id);
      navigate("/");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error && !run) {
    return <main className="page wide"><p className="error">{error}</p><Link to="/" className="back">← Back to projects</Link></main>;
  }
  if (!run) return <main className="page wide"><p className="muted">Loading…</p></main>;

  return (
    <main className="page wide">
      <Link to="/" className="back">← Back to projects</Link>
      <section className="card">
        <div className="run-head">
          <div className="page-head">
            <h1>{run.name ?? run.process_name ?? run.video_name}</h1>
            <p className="subtle small">
              {run.video_name} · uploaded {dateTime(run.created_at)} · {templateLabel(run, site)}
            </p>
          </div>
          <StatusPill status={run.status} />
        </div>

        {active && <Stages run={run} />}

        {run.status === "failed" && (
          <div className="notice notice-error">
            <p>{run.error}</p>
            {run.recording_deleted_at
              ? <p className="small">The recording was deleted when its retention period ended; upload it again to retry.</p>
              : site?.billing === "credits" && (run.refunded || run.credits === null)
                ? <p className="small">Create the project again to run it.</p>
                : <><button className="btn btn-dark" onClick={retry}>Retry</button>
                    <p className="small">
                      {site?.billing === "credits" ? "Retrying is free and continues" : "Retrying continues"} from where it stopped.
                    </p></>}
            {run.refunded && run.recording_deleted_at && <p className="small">Its credits were given back.</p>}
          </div>
        )}

        {run.status === "done" && (
          <>
            <div><a className="btn btn-dark btn-lg" href={api.documentUrl(run.id)} download>Download Word document</a></div>
            {model && <Overview model={model} />}
            {run.summary && (
              <dl className="stats">
                <div><dt>Steps</dt><dd>{run.summary.steps ?? "–"}</dd></div>
                <div><dt>Screenshots</dt><dd>{run.summary.screenshots ?? "–"}</dd></div>
                <div><dt>Recorded time</dt><dd>{duration(run.summary.recorded_seconds)}</dd></div>
                {site?.billing === "credits" ? <div><dt>Credits</dt><dd>{run.credits ?? "–"}</dd></div>
                  : run.cost_usd != null && (
                    <div title="Estimated from the tokens used and list prices, redos and assessment included">
                      <dt><Link to="/dashboard" className="stat-link">AI cost so far</Link></dt><dd>{usd(run.cost_usd)}</dd>
                    </div>)}
              </dl>
            )}
            {!!run.summary?.warnings.length && (
              <div className="notice">
                <strong>Worth checking</strong>
                <ul>{run.summary.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
              </div>
            )}
          </>
        )}
        {run.recording_deleted_at && run.status === "done" && (
          <p className="subtle small">
            The recording was deleted on {dateTime(run.recording_deleted_at)} (retention period). The document and its
            screenshots are kept.
          </p>
        )}
        {error && <p className="error">{error}</p>}
      </section>

      {run.status === "done" && (
        <ReviewCard run={run} onRedone={() => { load(); api.model(id).then(setModel, () => {}); }} />
      )}

      {run.roi && (run.roi.assessment ? <ViabilitySection roi={run.roi} />
        : run.status === "done" && <AssessPrompt run={run} onChange={setRun} />)}
      {run.roi && <RoiSection run={run} onChange={setRun} />}
      {run.status === "done" && <AutomationSpecSection run={run} />}

      {!active && (
        <div className="end">
          <button className="btn btn-danger-text" onClick={remove}>Delete this project</button>
        </div>
      )}
    </main>
  );
}

/** The process's summary, applications and inputs; the steps, rules and exceptions are in the document. */
function Overview({ model }: { model: ProcessModel }) {
  const applications = [...new Set(model.as_is.map((s) => s.application).filter(Boolean))];
  return (
    <div className="overview">
      <p>{model.project_proposal}</p>
      <dl className="facts">
        <dt>Applications</dt>
        <dd>{applications.length ? applications.map((a) => <span key={a} className="chip">{a}</span>) : "Not identified"}</dd>
        <dt>Inputs</dt>
        <dd>
          {model.inputs.length ? (
            <ul className="plain">
              {model.inputs.map((input) => (
                <li key={input.name}>
                  {input.name} <span className="subtle small">({INPUT_KINDS[input.kind]}{input.source ? `, from ${input.source}` : ""})</span>
                </li>
              ))}
            </ul>
          ) : "Not identified"}
        </dd>
      </dl>
    </div>
  );
}

/** automation.md (spec.py): the file an AI agent reads to build the automation, with a preview of it. */
function AutomationSpecSection({ run }: { run: Run }) {
  const [preview, setPreview] = useState<SpecPreview | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    api.automationPreview(run.id).then(setPreview, () => setPreview(null));
  }, [run.id, run.roi?.inputs]);
  return (
    <section className="card" aria-labelledby="sec-spec">
      <div className="card-head">
        <h2 id="sec-spec">Automation spec</h2>
        <p className="subtle">
          <strong className="text">automation.md</strong> is the file an AI agent reads to build the automation. It holds
          the process for a machine rather than a person: every step with what's on its screen, the decisions, business
          rules, exceptions and the questions the recording doesn't answer. It describes the process, not a tool, so
          the same file can become Python today or UiPath later.
        </p>
      </div>

      <ol className="spec-flow" aria-label="How it's used">
        <li>Recording</li>
        <li className="spec-flow-file">automation.md</li>
        <li>AI agent<span>plans and writes the code</span></li>
        <li>Automation<span>reviewed before it runs</span></li>
      </ol>

      {preview && (
        <div className={`code-file${open ? " open" : ""}`}>
          <div className="code-file-head">
            <span className="code-file-name">automation.md</span>
            <span className="subtle small">
              + {preview.screenshots} screenshot{preview.screenshots === 1 ? "" : "s"} ·{" "}
              {preview.open_questions
                ? `${preview.open_questions} open question${preview.open_questions === 1 ? "" : "s"} to answer before building`
                : "no open questions"}
            </span>
          </div>
          <pre tabIndex={0} aria-label="Preview of automation.md">{preview.markdown}</pre>
          <button type="button" className="code-file-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Show less" : "Show the whole file"}
          </button>
        </div>
      )}

      <div className="spec-actions">
        <a className="btn btn-dark" href={api.automationUrl(run.id)} download>Download automation spec</a>
        <span className="subtle small">A zip with automation.md and a screenshot of each step.</span>
      </div>
    </section>
  );
}

function Stages({ run }: { run: Run }) {
  // "reuse_run" stands for the three recording stages at once (a run made from an earlier one's captions).
  const current = run.stage === "reuse_run" ? run.stages.indexOf("transcribe") : run.stages.indexOf(run.stage ?? "");
  return (
    <div className="stages" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <ol>
        {run.stages.map((key, i) => {
          const state = run.status === "queued" ? "pending" : i < current ? "done" : i === current ? "current" : "pending";
          return (
            <li key={key} className={`stage stage-${state}`}>
              <span className="stage-dot" aria-hidden />
              {STAGE_LABELS[key] ?? key}
            </li>
          );
        })}
      </ol>
      <p className="muted small" aria-live="polite">
        {run.message ?? (run.status === "queued" ? "Waiting for the previous projects to finish" : "Starting")}
      </p>
      <p className="subtle small">
        A five-minute recording takes a few minutes. You can leave this page; the document keeps being made.
      </p>
    </div>
  );
}

/** Is the document right? Approve it, or say what's wrong (on /runs/<id>/review) and have it done again with
those notes: the redo runs on the server for a minute or so, then each note gets an answer. */
function ReviewCard({ run, onRedone }: { run: Run; onRedone: () => void }) {
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.review(run.id).then(setReview, (e) => setError(e.message));
  }, [run.id]);
  // Coming back from the review page (/runs/<id>#review): straight to this card.
  useEffect(() => {
    if (review && window.location.hash === "#review") document.getElementById("review")?.scrollIntoView({ block: "center" });
  }, [review?.status]);
  // While redoing: ask until it's done, then show the new version everywhere on the page.
  const redoing = review?.status === "redoing";
  usePolling(() => {
    api.review(run.id).then((r) => {
      setReview(r);
      if (r.status !== "redoing") onRedone();
    }, () => {});
  }, 3000, redoing);

  async function act(call: (id: string) => Promise<Review>, after?: () => void) {
    setBusy(true);
    setError(null);
    try {
      setReview(await call(run.id));
      after?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!review) return error ? <p className="error">{error}</p> : null;
  const { steps, general } = review.feedback;
  const marked = new Set(steps.map((s) => s.number)).size;
  const last = review.versions.at(-1);
  const justRedone = review.status === "pending" && last && last.number === review.version;

  if (review.status === "approved") {
    return (
      <section className="card review-card approved" id="review" aria-labelledby="sec-review">
        <div className="verdict-banner verdict-banner-good">
          <span className="verdict-mark" aria-hidden="true">✓</span>
          <div>
            <h2 id="sec-review" className="verdict-title">Approved</h2>
            <p>
              You approved this document on {dateTime(review.approved_at!)}
              {review.version > 1 ? ` (version ${review.version})` : ""}.
            </p>
          </div>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <div>
          <button type="button" className="link-btn" style={{ marginLeft: 0 }} disabled={busy}
                  onClick={() => act(api.reopen)}>Undo the approval</button>
        </div>
      </section>
    );
  }

  if (redoing) {
    return (
      <section className="card review-card" id="review" aria-labelledby="sec-review" aria-busy="true">
        <div className="redo-progress">
          <span className="spinner" aria-hidden="true" />
          <div>
            <h2 id="sec-review" className="verdict-title">Redoing the document with your notes…</h2>
            <p className="subtle" aria-live="polite">
              Pidi is looking at the steps you marked again, in the recording. It takes about a minute; you can leave
              this page.
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="card review-card" id="review" aria-labelledby="sec-review">
      <div className="card-head">
        <h2 id="sec-review">{justRedone ? `Version ${review.version} is ready` : "Is this document right?"}</h2>
        <p className="subtle">
          {justRedone
            ? "Pidi did the document again with your notes. Here's what came of each one; download the Word document to read it."
            : review.status === "changes_requested"
              ? "You've said what's wrong. Redo the document with your notes, or edit them first."
              : "Read the Word document, then approve it, or say what's wrong so Pidi can do it again with your notes."}
        </p>
      </div>

      {justRedone && (
        <>
          <ul className="redo-answers">
            {last.answers.map((a, i) => (
              <li key={i} className={`redo-answer ${a.outcome}`}>
                <span className="redo-mark" aria-hidden="true">
                  {a.outcome === "fixed" ? "✓" : a.outcome === "not_in_recording" ? "✕" : "?"}
                </span>
                <span>
                  <strong>{a.label}:</strong>{" "}
                  {a.outcome === "fixed" ? "fixed" : a.outcome === "not_in_recording" ? "not in the recording" : "unclear"}
                  <span className="subtle"> · {a.explanation}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="small">
            {last.changed.length
              ? <>New or changed: {last.changed.map((n) => `step ${n}`).join(", ")}. <Link to={`/runs/${run.id}/review`}>See the steps</Link></>
              : "No step changed."}{" "}
            <button type="button" className="link-btn" disabled={busy}
                    onClick={() => act(api.undoRedo, onRedone)}>Undo this redo</button>
          </p>
        </>
      )}

      {review.status === "changes_requested" && (
        <ul className="review-summary">
          {general && <li><strong>In general:</strong> {general}</li>}
          {steps.map((s) => (
            <li key={`${s.number}-${s.issue}`}>
              <strong>Step {s.number}:</strong> {ISSUES.find((i) => i.issue === s.issue)?.label}
              {s.note && <span className="subtle"> · {s.note}</span>}
            </li>
          ))}
        </ul>
      )}
      {review.redo_error && review.status === "changes_requested" && (
        <p className="error" role="alert">The redo didn't work: {review.redo_error}</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions" style={{ justifyContent: "flex-start" }}>
        {review.status === "changes_requested" ? (
          <>
            <button type="button" className="btn btn-dark" disabled={busy || review.redos_left === 0}
                    onClick={() => act(api.redo)}>Redo with my notes</button>
            <Link to={`/runs/${run.id}/review`} className="btn btn-outline">
              Edit my notes ({marked} step{marked === 1 ? "" : "s"})
            </Link>
            <button type="button" className="btn btn-outline" disabled={busy} onClick={() => act(api.approve)}>
              Approve it as it is
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn btn-dark" disabled={busy} onClick={() => act(api.approve)}>Approve</button>
            <Link to={`/runs/${run.id}/review`} className="btn btn-outline">Ask for changes</Link>
          </>
        )}
      </div>
      {review.redos_left !== null && review.status === "changes_requested" && (
        <p className="subtle small">
          {review.redos_left === 0 ? "This project can't be done again any more; create it again for more."
            : `${review.redos_left} redo${review.redos_left === 1 ? "" : "s"} left for this project, free.`}
        </p>
      )}
    </section>
  );
}
