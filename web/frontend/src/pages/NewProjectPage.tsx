import { useEffect, useState, type DragEvent, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, uploadRecording, type Estimate, type Keys, type Quote, type RoiTemplate, type Template } from "../api";
import { usd } from "./CostsPage";
import { NoCreditsDialog, UploadIcon, WizardSteps } from "../components";
import { bytes } from "../format";
import { retentionText, useSite } from "../site";

const ACCEPT = ".mp4,.mov,.mkv,.webm,.avi,.m4v,.wmv";
const LANGUAGE_KEY = "pidi.language"; // the last language chosen, offered again on this browser
const OTHER = "other";

function savedLanguage(): string {
  try {
    return localStorage.getItem(LANGUAGE_KEY) || "en";
  } catch {
    return "en";
  }
}

/** The video's length in seconds, read by the browser; null when it can't play the format (e.g. WMV, AVI). */
function videoSeconds(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    const done = (seconds: number | null) => {
      URL.revokeObjectURL(url);
      resolve(seconds);
    };
    video.preload = "metadata";
    video.onloadedmetadata = () => done(Number.isFinite(video.duration) ? video.duration : null);
    video.onerror = () => done(null);
    video.src = url;
  });
}

const plural = (n: number) => `${n} credit${n === 1 ? "" : "s"}`;

export default function NewProjectPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [roiTemplates, setRoiTemplates] = useState<RoiTemplate[]>([]);
  const [roiTemplateId, setRoiTemplateId] = useState("");
  const [language, setLanguage] = useState(savedLanguage); // a code of the list, or a language's name
  const [otherOpen, setOtherOpen] = useState(false);
  const [balance, setBalance] = useState<number | null>(null);
  const [seconds, setSeconds] = useState<number | null>(null); // of the chosen video, as the browser reads it
  const [quote, setQuote] = useState<Quote | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [noCredits, setNoCredits] = useState(false); // the "not enough credits" dialog
  const [step, setStep] = useState<1 | 2>(1); // 1: the project, 2: its recording
  const [keys, setKeys] = useState<Keys | null>(null); // with billing "keys": the person's own
  const [keyId, setKeyId] = useState(""); // the key it runs on; "" with none, or on the server's key
  const [estimate, setEstimate] = useState<Estimate | null>(null); // what it will roughly cost on that key
  const site = useSite();
  const usesCredits = site?.billing === "credits";

  useEffect(() => {
    api.templates().then(setTemplates, () => setTemplates([]));
    api.roiTemplates().then(setRoiTemplates, () => setRoiTemplates([]));
  }, []);

  useEffect(() => {
    if (!site) return;
    if (site.billing === "credits") {
      api.credits().then((c) => setBalance(c.balance), (e) => setError(e.message));
    } else {
      api.keys().then((k) => {
        setKeys(k);
        setKeyId(k.keys.length ? k.keys[k.keys.length - 1].id : ""); // the newest key
      }, (e) => setError(e.message));
    }
  }, [site]);

  useEffect(() => {
    setSeconds(null);
    if (!file) return;
    let current = true;
    videoSeconds(file).then((s) => current && setSeconds(s));
    return () => { current = false; };
  }, [file]);

  // The price, as soon as the video's length is known; the upload measures the video again and charges that.
  const quoteLanguage = templateId ? null : language.trim();
  useEffect(() => {
    setQuote(null);
    if (!usesCredits || seconds === null || quoteLanguage === "") return;
    let current = true;
    api.quote(seconds, quoteLanguage).then((q) => {
      if (!current) return;
      setQuote(q);
      setBalance(q.balance);
      setNoCredits(q.credits > q.balance);
    }, () => { /* e.g. a language name still being typed; the price shows once it's valid */ });
    return () => { current = false; };
  }, [seconds, quoteLanguage, usesCredits]);

  // With own keys: roughly what the recording will cost, once its length and the key are known.
  useEffect(() => {
    setEstimate(null);
    if (usesCredits || seconds === null || keys === null || (keys.keys.length === 0 && !keys.server_key)) return;
    let current = true;
    api.estimate(seconds, keyId || null).then((e) => current && setEstimate(e), () => {});
    return () => { current = false; };
  }, [seconds, keyId, keys, usesCredits]);

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (step === 1) return setStep(2); // the browser already checked step 1's required fields
    if (!file) {
      setError("Choose the video of the process first.");
      return;
    }
    setError(null);
    setProgress(0);
    try {
      const run = await uploadRecording(file, {
        name, templateId: templateId || null, language: language.trim(), roiTemplateId: roiTemplateId || null,
        apiKeyId: keyId || null,
      }, setProgress);
      navigate(`/runs/${run.id}`);
    } catch (err) {
      // The server measured the video and found it costs more than the balance (402): the dialog says so.
      if (err instanceof ApiError && err.status === 402) setNoCredits(true);
      else setError((err as Error).message);
      setProgress(null);
    }
  }

  const uploading = progress !== null;
  const short = quote !== null && quote.credits > quote.balance;
  // With own keys: none connected, and the site's key isn't offered.
  const noKey = !usesCredits && keys !== null && keys.keys.length === 0 && !keys.server_key;
  const template = templates.find((t) => t.id === templateId);
  const languages = site?.languages ?? [];
  const listed = languages.find((l) => l.code === language);
  // A remembered name that isn't in the list is shown as "Other", typed in.
  const other = otherOpen || (site !== null && !listed);
  const machineTranslated = other || Boolean(listed?.translated);

  function chooseLanguage(value: string) {
    setLanguage(value);
    try {
      localStorage.setItem(LANGUAGE_KEY, value.trim());
    } catch {
      /* not remembered; nothing else changes */
    }
  }

  function pickLanguage(value: string) {
    setOtherOpen(value === OTHER);
    chooseLanguage(value === OTHER ? "" : value);
  }

  return (
    <main className="page narrow">
      <div className="page-head" style={{ gap: 12 }}>
        <Link to="/" className="back">← Back to projects</Link>
        <h1>New project</h1>
        <p>Upload the recording and Pidi will turn it into a PDD.</p>
      </div>

      <form className="card form" style={{ padding: 36, gap: 28 }} onSubmit={submit}>
        <WizardSteps step={step} />

        {step === 1 ? (
          <>
            <div className="field">
              <label htmlFor="np-name">Project name</label>
              <input id="np-name" type="text" placeholder="e.g. Invoice approval in SAP" value={name}
                     onChange={(e) => setName(e.target.value)} maxLength={120} />
            </div>

            <div className="field">
              <label htmlFor="np-template">PDD document template</label>
              <p className="hint">The structure your document will follow.</p>
              <select id="np-template" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                <option value="">Pidi template (default)</option>
                {templates.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.language})</option>)}
              </select>
              <p className="after">Using your company's own format? <Link to="/templates">Add a template</Link></p>
            </div>

            <div className="field">
              <label htmlFor="np-language">Document language</label>
              <p className="hint">
                {template ? "A company template is written in its own language." : "The language the PDD is written in."}
              </p>
              {template ? (
                <select id="np-language" disabled value="template">
                  <option value="template">{template.language}</option>
                </select>
          ) : (
            <>
                  <select id="np-language" value={other ? OTHER : language} onChange={(e) => pickLanguage(e.target.value)}>
                    {languages.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
                    <option value={OTHER}>Other…</option>
                  </select>
                  {other && (
                    <input type="text" aria-label="Language name" placeholder="Type the language, e.g. Tagalog"
                           required maxLength={40} value={language} autoFocus={otherOpen}
                           onChange={(e) => chooseLanguage(e.target.value)} style={{ marginTop: 10 }} />
                  )}
                  {machineTranslated && (
                    <p className="after">
                      Pidi's template is translated into this language automatically the first time it's used, so its
                      headings and labels may read less naturally than in English or Portuguese.
                    </p>
                  )}
            </>
              )}
            </div>

            <div className="field">
              <label htmlFor="np-roi">ROI template</label>
              <p className="hint">Your company's assumptions for working out what automating the process saves.</p>
              <select id="np-roi" value={roiTemplateId} onChange={(e) => setRoiTemplateId(e.target.value)}>
                <option value="">None</option>
                {roiTemplates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
              <p className="after">
                Every project gets an assessment of whether it's worth automating; with an ROI template, also its
                savings, payback and ROI. <Link to="/roi/templates/new">Create an ROI template</Link>
              </p>
            </div>
          </>
        ) : (
          <>

            <div className="field">
              <label htmlFor="np-video">Video</label>
              <p className="hint">A screen recording of the process. Narration is optional but helps.</p>
              <label htmlFor="np-video" className={`dropzone${dragging ? " dragging" : ""}`}
                     onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                     onDragLeave={() => setDragging(false)} onDrop={onDrop}>
                <span className="dropzone-icon"><UploadIcon /></span>
                {file ? (
              <>
                    <strong>{file.name}</strong>
                    <span className="subtle small">{bytes(file.size)} · <span className="u">choose another</span></span>
              </>
            ) : (
              <>
                    <strong>Drop your video here or <span className="u">browse</span></strong>
                    <span className="subtle small">MP4, MOV, MKV, WebM, AVI or WMV</span>
              </>
                )}
              </label>
              <input id="np-video" className="sr-only" type="file" accept={ACCEPT} disabled={uploading}
                     onChange={(e) => { setError(null); setFile(e.target.files?.[0] ?? null); }} />
              {uploading && (
                <div className="progress" style={{ marginTop: 14 }}>
                  <div className="bar"><div style={{ width: `${Math.round(progress * 100)}%` }} /></div>
                  <span className="subtle small" aria-live="polite">
                    {progress < 1 ? `Uploading… ${Math.round(progress * 100)}%` : "Checking the video…"}
                  </span>
                </div>
              )}
            </div>

            {usesCredits ? (
            <div className="field">
              <p className="field-title">Price</p>
              <div className={`price${short ? " short" : ""}`} aria-live="polite">
                <strong>
                  {quote ? `This recording: ${plural(quote.credits)}`
                    : file && seconds === null ? "Priced when the video is uploaded"
                    : "Choose a video to see its price"}
                </strong>
                <span className="subtle small">
                  {balance === null ? "" : `You have ${plural(balance)}. `}
                  {site && `${plural(site.credits.per_minute)} per started minute of video${
                    site.credits.translation ? `, plus ${plural(site.credits.translation)} the first time Pidi's template is translated into a language` : ""}.`}
                </span>
              </div>
              <p className="after">
                {short
                  ? "You don't have enough credits for this recording."
                  : "If the project fails, retrying it is free."}{" "}
                <Link to="/credits">{short ? "Buy credits" : "Your credits"}</Link>
              </p>
            </div>
            ) : (
              <div className="field">
                <label htmlFor="np-key">AI provider</label>
                <p className="hint">The project runs on your own provider, which bills you for it.</p>
                {keys && keys.keys.length > 0 ? (
                  <select id="np-key" value={keyId} onChange={(e) => setKeyId(e.target.value)}>
                    {keys.keys.map((k) => (
                      <option key={k.id} value={k.id}>
                        {[keys.providers.find((p) => p.id === k.provider)?.label ?? k.provider,
                          k.name !== keys.providers.find((p) => p.id === k.provider)?.label ? k.name : "",
                          k.hint, k.model ?? ""].filter(Boolean).join(" · ")}
                      </option>
                    ))}
                  </select>
                ) : keys?.server_key ? (
                  <p className="price"><strong>This site's key</strong>
                    <span className="subtle small">You haven't connected a key of your own, so it runs on the site's.</span></p>
                ) : keys && (
                  <p className="price short"><strong>No provider connected yet</strong>
                    <span className="subtle small">Connect your AI provider to create projects.</span></p>
                )}
                {estimate && <EstimateLine estimate={estimate} />}
                <p className="after">
                  {noKey ? "" : "Need another one? "}
                  <Link to="/account#api-key">{noKey ? "Connect a provider on the Account page" : "Manage your providers"}</Link>
                </p>
              </div>
            )}

            <div className="field">
              <label className="check">
                <input type="checkbox" required checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                <span>
                  I'm allowed to upload this recording: the people it shows were told, and the company it belongs to agrees
                  to it being processed, including by the AI provider it runs on.
                </span>
              </label>
              <p className="after">
                The recording is deleted {site ? retentionText(site.retention_days) : "after a while"}; the document and its
                screenshots stay. <Link to="/privacy" target="_blank">Privacy notice</Link>
              </p>
            </div>
          </>
        )}

        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions" style={{ paddingTop: 4 }}>
          {step === 1 ? (
            <>
              <Link to="/" className="btn btn-outline">Cancel</Link>
              <button className="btn btn-dark">Next</button>
            </>
          ) : (
            <>
              <button type="button" className="btn btn-outline" disabled={uploading} onClick={() => setStep(1)}>Back</button>
              <button className="btn btn-dark" disabled={uploading || short || noKey || !consent}>Create a new project</button>
            </>
          )}
        </div>
      </form>
      <NoCreditsDialog open={noCredits} onClose={() => setNoCredits(false)} />
    </main>
  );
}

/** "About $0.33 (between $0.23 and $0.43)", and what it's based on. */
function EstimateLine({ estimate: e }: { estimate: Estimate }) {
  if (e.basis === "free") return <p className="estimate"><strong>Free:</strong> it runs on this server's Ollama.</p>;
  if (e.basis === "unknown" || e.mid === null) {
    return <p className="estimate subtle small">No estimate: Pidi has no price for {e.unpriced.join(", ")}.</p>;
  }
  return (
    <p className="estimate" aria-live="polite">
      <strong>About {usd(e.mid)}</strong>{" "}
      <span className="subtle small">
        (between {usd(e.low!)} and {usd(e.high!)},{" "}
        {e.basis === "history"
          ? `from your ${e.projects === 1 ? "last project" : `${e.projects} projects`} on these models)`
          : "a rough estimate from typical projects)"}
        . Your provider bills it; redos cost a little more.
      </span>
    </p>
  );
}
