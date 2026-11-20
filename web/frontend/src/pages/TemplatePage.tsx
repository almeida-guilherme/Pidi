import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type TemplateAction, type TemplateBlock, type TemplateDetail } from "../api";
import { TEMPLATE_ACTIONS, dateTime } from "../format";

export default function TemplatePage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [template, setTemplate] = useState<TemplateDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null); // the block being changed

  useEffect(() => {
    api.template(id).then(setTemplate, (e: ApiError) =>
      setError(e.status === 404 ? "This template doesn't exist." : e.message));
  }, [id]);

  async function remove() {
    if (!confirm("Delete this template? Documents already made with it are not affected.")) return;
    try {
      await api.removeTemplate(id);
      navigate("/templates");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function undo(b: TemplateBlock) {
    try {
      setTemplate(await api.changeTemplateBlock(id, b.index, b.original!.action, b.original!.purpose));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error && !template) {
    return <main className="page wide"><p className="error">{error}</p><Link to="/templates" className="back">← All templates</Link></main>;
  }
  if (!template) return <main className="page wide"><p className="muted">Loading…</p></main>;

  const removed = template.blocks.filter((b) => b.action === "remove");
  const filled = template.blocks.filter((b) => TEMPLATE_ACTIONS[b.action]?.tone === "fill" && b.kind !== "steps");

  return (
    <main className="page wide">
      <Link to="/templates" className="back">← All templates</Link>
      <section className="card">
        <div className="page-head">
          <h1 style={{ fontSize: 34 }}>{template.name}</h1>
          <p className="subtle small">{template.language} · added {dateTime(template.created_at)}</p>
        </div>
        <dl className="stats">
          <div><dt>Parts filled per process</dt><dd>{filled.length}</dd></div>
          <div><dt>Removed</dt><dd>{removed.length}</dd></div>
          <div><dt>Placeholders</dt><dd>{template.placeholders.length}</dd></div>
          <div><dt>Steps written as</dt><dd className="mono">{template.step_label}…</dd></div>
        </dl>
        <p className="small muted">
          Anything the recording doesn't tell (contacts, versions, volumes…) is written as{" "}
          <mark>{template.missing_label}</mark> so you can complete it.
        </p>
      </section>

      {template.placeholders.length > 0 && (
        <section className="card">
          <h2>Placeholders</h2>
          <ul className="plain">
            {template.placeholders.map((p) => (
              <li key={p.text}><code>{p.text}</code> <span className="subtle small">— {p.meaning}</span></li>
            ))}
          </ul>
        </section>
      )}

      <section className="card">
        <div className="card-head">
          <h2>How the template is used</h2>
          <p>
            Every part of the template, in order, with what happens to it. If Pidi got a part wrong, change it:
            projects created afterwards follow your change, and projects already made keep theirs.
          </p>
        </div>
        <div className="table-scroll">
          <table className="blocks">
            <thead><tr><th>Part of the template</th><th>What happens</th></tr></thead>
            <tbody>
              {template.blocks.map((b) => {
                const action = TEMPLATE_ACTIONS[b.action] ?? { label: b.action, tone: "keep" };
                return (
                  <tr key={`${b.kind}-${b.index}`} className={`block-${action.tone}${b.kind === "heading" ? " block-heading" : ""}`}>
                    <td>
                      {b.kind === "table" && <span className="subtle small">Table: </span>}
                      {b.kind === "steps" ? <em>New: the detailed steps</em> : b.text || <span className="muted">(empty)</span>}
                    </td>
                    <td>
                      {editing === b.index && b.kind !== "steps" ? (
                        <BlockEditor block={b} templateId={id} onSaved={(t) => { setTemplate(t); setEditing(null); }}
                                     onCancel={() => setEditing(null)} />
                      ) : (
                        <>
                          <span className={`tag tag-${action.tone}`}>{action.label}</span>
                          {(b.purpose || b.reason) && <div className="subtle">{b.purpose || b.reason}</div>}
                          {b.original && (
                            <div className="subtle small block-changed">
                              Changed by you; Pidi had “{TEMPLATE_ACTIONS[b.original.action]?.label ?? b.original.action}”.{" "}
                              <button type="button" className="link-btn" onClick={() => undo(b)}>Undo</button>
                            </div>
                          )}
                          {b.editable && editing === null && (
                            <button type="button" className="link-btn block-change" onClick={() => setEditing(b.index)}>
                              Change
                            </button>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </section>

      <div className="end">
        <button className="btn btn-danger-text" onClick={remove}>Delete this template</button>
      </div>
    </main>
  );
}

const CHOICES: Record<string, { action: TemplateAction; label: string; hint: string }[]> = {
  paragraph: [
    { action: "keep", label: "Keep as it is", hint: "Fixed text that suits any process." },
    { action: "remove", label: "Remove", hint: "Instructions, or leftovers of another process." },
    { action: "write", label: "Write it for each process", hint: "Pidi writes this part from the recording." },
  ],
  table: [
    { action: "keep", label: "Keep as it is", hint: "A table that suits any process." },
    { action: "remove", label: "Remove", hint: "An example from another process." },
    { action: "table_rows", label: "Fill its rows", hint: "One row per item of the process, e.g. per system." },
    { action: "table_answers", label: "Fill its answers", hint: "A column of answers to the questions in the table." },
  ],
};

/** Changes what happens to one part of the template. */
function BlockEditor({ block, templateId, onSaved, onCancel }: {
  block: TemplateBlock; templateId: string; onSaved: (t: TemplateDetail) => void; onCancel: () => void;
}) {
  const choices = CHOICES[block.kind === "table" ? "table" : "paragraph"];
  const [action, setAction] = useState<TemplateAction>(block.action);
  const [purpose, setPurpose] = useState(block.purpose);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fills = action === "write" || action === "table_rows" || action === "table_answers";

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.changeTemplateBlock(templateId, block.index, action, fills ? purpose : ""));
    } catch (err) {
      setError((err as Error).message);
      setSaving(false);
    }
  }

  return (
    <form className="block-editor" onSubmit={save}>
      <fieldset>
        <legend className="sr-only">What happens to this part</legend>
        {choices.map((c) => (
          <label key={c.action} className="block-choice">
            <input type="radio" name={`block-${block.index}`} checked={action === c.action}
                   onChange={() => setAction(c.action)} />
            <span><strong>{c.label}</strong><span className="subtle small">{c.hint}</span></span>
          </label>
        ))}
      </fieldset>
      {fills && (
        <label className="block-purpose">
          <span className="small">What it must contain for each process</span>
          <textarea rows={2} maxLength={300} required value={purpose} onChange={(e) => setPurpose(e.target.value)}
                    placeholder={action === "table_rows" ? "e.g. One row per system used in the process"
                      : "e.g. A short description of the process"} />
        </label>
      )}
      {error && <p className="error small" role="alert">{error}</p>}
      <div className="block-actions">
        <button className="btn btn-dark btn-sm" disabled={saving}>{saving ? "Saving…" : "Save"}</button>
        <button type="button" className="btn btn-outline btn-sm" disabled={saving} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
