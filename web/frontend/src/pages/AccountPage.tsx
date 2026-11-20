import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, type Credits, type Keys } from "../api";
import { useAuth } from "../App";
import { initials } from "../components";
import { dateTime } from "../format";
import { useSite } from "../site";

export default function AccountPage() {
  const { user } = useAuth();
  const site = useSite();

  if (!user || !site) return null;
  const credits = site.billing === "credits";
  return (
    <main className="page medium">
      <div className="page-head">
        <h1>Account</h1>
        <p>Your info, {credits ? "credits" : "AI provider"} and data.</p>
      </div>
      <InfoSection />
      {credits ? <CreditsSection /> : <KeysSection />}
      <DataSection />
    </main>
  );
}

function useStatus() {
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);
  async function run(action: () => Promise<string>) {
    setBusy(true);
    setStatus({});
    try {
      setStatus({ ok: await action() });
    } catch (e) {
      setStatus({ error: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  const message = status.ok ? <p className="notice-ok" role="status">{status.ok}</p>
    : status.error ? <p className="error" role="alert">{status.error}</p> : null;
  return { busy, run, message };
}

function InfoSection() {
  const { user, setUser } = useAuth();
  const [name, setName] = useState(user?.name ?? "");
  const { busy, run, message } = useStatus();

  function save(e: FormEvent) {
    e.preventDefault();
    run(async () => {
      setUser(await api.updateProfile(name));
      return "Saved.";
    });
  }

  if (!user) return null;
  return (
    <form className="card" aria-labelledby="sec-info" onSubmit={save}>
      <div className="section-head">
        <span className="avatar large" aria-hidden="true">{initials(user)}</span>
        <h2 id="sec-info">Your info</h2>
      </div>
      <div className="grid-2">
        <div className="field">
          <label htmlFor="ac-name">Name</label>
          <input id="ac-name" type="text" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ac-email">Email</label>
          <input id="ac-email" type="email" value={user.email} readOnly aria-describedby="ac-email-hint" />
          <p id="ac-email-hint" className="subtle small">From the Google account you log in with.</p>
        </div>
      </div>
      {message}
      <div className="actions"><button className="btn btn-dark" disabled={busy}>Save changes</button></div>
    </form>
  );
}

/** People's own AI provider keys (billing "keys"): projects run on them, so the provider bills their owner. */
function KeysSection() {
  const [keys, setKeys] = useState<Keys | null>(null);
  const [provider, setProvider] = useState("openai");
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const { busy, run, message } = useStatus();
  const { hash } = useLocation();

  useEffect(() => {
    api.keys().then(setKeys, () => setKeys({ keys: [], server_key: false, providers: [] }));
  }, []);
  useEffect(() => {
    // Linked from the new-project page as /account#api-key.
    if (hash === "#api-key" && keys) document.getElementById("api-key")?.scrollIntoView();
  }, [hash, keys]);

  const chosen = keys?.providers.find((p) => p.id === provider);
  const label = (id: string) => keys?.providers.find((p) => p.id === id)?.label ?? id;

  function connect(e: FormEvent) {
    e.preventDefault();
    run(async () => {
      await api.addKey(provider, name, chosen?.needs_key ? key : "", model);
      setKeys(await api.keys());
      setName("");
      setKey("");
      setModel("");
      return chosen?.needs_key ? "Key connected." : "Connected.";
    });
  }

  function remove(id: string, text: string) {
    if (!confirm(`Remove “${text}”? Projects waiting to run on it will stop.`)) return;
    run(async () => {
      await api.removeKey(id);
      setKeys(await api.keys());
      return "Removed.";
    });
  }

  return (
    <section className="card" id="api-key" aria-labelledby="sec-api">
      <div className="card-head">
        <h2 id="sec-api">AI provider</h2>
        <p>
          Pidi processes your recordings with your own AI provider, so it bills you directly for what each project
          uses. Connect a key from OpenAI, Anthropic (Claude) or Google (Gemini)
          {keys?.providers.some((p) => p.id === "ollama") ? ", or use this server's Ollama" : ""}.
        </p>
      </div>

      {keys?.keys.map((k) => (
        <div key={k.id} className="key-row">
          <div className="who">
            <span className="dot" aria-hidden="true" />
            <div>
              <div className="label">{label(k.provider)}{k.name !== label(k.provider) ? ` · ${k.name}` : ""}</div>
              <div className="hint">
                {[k.hint, k.model ? `model ${k.model}` : "default models", `connected ${dateTime(k.created_at)}`]
                  .filter(Boolean).join(" · ")}
              </div>
            </div>
          </div>
          <button type="button" className="btn btn-danger-text" disabled={busy} onClick={() => remove(k.id, k.name)}>
            Remove
          </button>
        </div>
      ))}
      {keys && keys.keys.length === 0 && (
        <p className="notice">
          {keys.server_key
            ? "No provider of your own yet: your projects run on this site's OpenAI key until you connect one."
            : "No provider connected yet. Connect one to create projects."}
        </p>
      )}

      <form className="divider" onSubmit={connect}>
        <h3>Connect a provider</h3>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="ac-provider">Provider</label>
            <select id="ac-provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
              {keys?.providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
            {chosen && (
              <p className="subtle small">
                {chosen.needs_key
                  ? <>Create a key at <a href={chosen.keys_url} target="_blank" rel="noreferrer">{new URL(chosen.keys_url).host}</a>.</>
                  : <>Runs on models installed on this server's Ollama; no key needed. <a href={chosen.keys_url} target="_blank" rel="noreferrer">Models</a></>}
              </p>
            )}
          </div>
          {chosen?.needs_key !== false && (
            <div className="field">
              <label htmlFor="ac-key">API key</label>
              <input id="ac-key" type="password" placeholder="Paste the key" autoComplete="off" spellCheck={false}
                     required value={key} onChange={(e) => setKey(e.target.value)} />
            </div>
          )}
          <div className="field">
            <label htmlFor="ac-model">Model <span className="subtle small">(optional)</span></label>
            <input id="ac-model" type="text" spellCheck={false} maxLength={120} value={model}
                   placeholder={chosen ? `Default: ${chosen.models.join(" and ")}` : ""}
                   onChange={(e) => setModel(e.target.value)} />
            <p className="subtle small">
              Leave it empty for the defaults. A model of your own must read images
              {chosen?.id === "ollama" ? " and be installed on the server" : ""}.
            </p>
          </div>
          <div className="field">
            <label htmlFor="ac-keyname">Name <span className="subtle small">(optional)</span></label>
            <input id="ac-keyname" type="text" placeholder="e.g. Work" maxLength={60} value={name}
                   onChange={(e) => setName(e.target.value)} />
          </div>
        </div>
        <p className="subtle small">
          Pidi checks the {chosen?.needs_key === false ? "connection" : "key"} first. Keys are kept encrypted and never
          shown again: only their first and last characters. With Claude, Gemini or Ollama, the narration is read by
          Whisper on this server.
        </p>
        {message}
        <div className="actions"><button className="btn btn-dark" disabled={busy || !keys}>{busy ? "Checking…" : "Connect"}</button></div>
      </form>
    </section>
  );
}

const KIND_LABELS = { grant: "Credits added", purchase: "Bought", charge: "Project", refund: "Given back" };

function CreditsSection() {
  const [credits, setCredits] = useState<Credits | null>(null);
  const [error, setError] = useState<string | null>(null);
  const site = useSite();
  const { hash } = useLocation();

  useEffect(() => {
    api.credits().then(setCredits, (e) => setError(e.message));
  }, []);

  useEffect(() => {
    // Linked from the new-project page as /account#credits.
    if (hash === "#credits" && credits) document.getElementById("credits")?.scrollIntoView();
  }, [hash, credits]);

  return (
    <section className="card" id="credits" aria-labelledby="sec-credits">
      <div className="card-head">
        <h2 id="sec-credits">Credits</h2>
        <p>
          Projects are paid with credits
          {site ? `: ${site.credits.per_minute} per started minute of video` : ""}. If a project fails, retrying it is
          free.
        </p>
      </div>
      <div><Link to="/credits" className="btn btn-outline">Buy credits</Link></div>
      {error && <p className="error" role="alert">{error}</p>}
      {credits && (
        <>
          <p className="balance"><strong>{credits.balance}</strong> credit{credits.balance === 1 ? "" : "s"} left</p>
          {credits.entries.length > 0 && (
            <table className="ledger">
              <caption className="sr-only">Credit history, newest first</caption>
              <thead><tr><th scope="col">Date</th><th scope="col">What</th><th scope="col">Credits</th></tr></thead>
              <tbody>
                {credits.entries.map((e, i) => (
                  <tr key={i}>
                    <td className="subtle">{dateTime(e.created_at)}</td>
                    <td>
                      <span className="subtle">{KIND_LABELS[e.kind]}</span>{e.note && " · "}
                      {e.run_id && e.kind === "charge" ? <Link to={`/runs/${e.run_id}`}>{e.note}</Link> : e.note}
                    </td>
                    <td className={e.amount > 0 ? "plus-amount" : "minus-amount"}>
                      {e.amount > 0 ? `+${e.amount}` : `−${-e.amount}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}

function DataSection() {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [email, setEmail] = useState("");
  const { busy, run, message } = useStatus();
  const site = useSite();

  function remove(e: FormEvent) {
    e.preventDefault();
    run(async () => {
      await api.deleteAccount(email);
      setUser(null);
      navigate("/", { replace: true });
      return "Account deleted.";
    });
  }

  return (
    <section className="card" aria-labelledby="sec-data">
      <h2 id="sec-data">Your data</h2>
      <p className="muted">
        Download what Pidi keeps about you (account, projects, templates, {site?.billing === "keys" ? "API keys" : "credits"}), or delete your account
        with everything in it. See the <Link to="/privacy">privacy notice</Link>.
      </p>
      <div className="actions" style={{ justifyContent: "flex-start" }}>
        <a className="btn btn-outline" href={api.exportUrl} download>Download my data</a>
        {!confirming && (
          <button type="button" className="btn btn-danger-text" onClick={() => setConfirming(true)}>Delete my account</button>
        )}
      </div>
      {confirming && (
        <form className="danger-zone" onSubmit={remove}>
          <p>
            <strong>This deletes your account, every project (recordings, documents, screenshots), your templates and
            {site?.billing === "keys" ? " API keys." : " credits, including credits you bought."}</strong> It can't be undone. Download the documents you want to keep first.
          </p>
          <div className="field">
            <label htmlFor="ac-delete-email">Type your email ({user?.email}) to confirm</label>
            <input id="ac-delete-email" type="email" autoComplete="off" required value={email}
                   onChange={(e) => setEmail(e.target.value)} />
          </div>
          {message}
          <div className="actions">
            <button type="button" className="btn btn-outline" onClick={() => setConfirming(false)}>Cancel</button>
            <button className="btn btn-danger" disabled={busy}>{busy ? "Deleting…" : "Delete my account"}</button>
          </div>
        </form>
      )}
    </section>
  );
}
