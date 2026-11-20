import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Currency, type Indicator, type RoiAssumptions } from "../api";
import { WizardSteps } from "../components";
import { CURRENCIES, DEFAULT_ASSUMPTIONS, INDICATORS } from "../roi";

type NumberField = "hourly_cost" | "build_cost" | "monthly_cost" | "months" | "hours_per_fte";
type Form = Record<NumberField, string> & { name: string; currency: Currency; indicators: Indicator[] };

const toForm = (name: string, a: RoiAssumptions): Form => ({
  name, currency: a.currency, indicators: a.indicators,
  hourly_cost: a.hourly_cost?.toString() ?? "", build_cost: a.build_cost?.toString() ?? "",
  monthly_cost: a.monthly_cost?.toString() ?? "", months: String(a.months), hours_per_fte: String(a.hours_per_fte),
});

const num = (text: string) => (text.trim() === "" || Number.isNaN(Number(text)) ? null : Number(text));

/** An ROI template, new (/roi/templates/new) or existing: a company's assumptions and the indicators it reports. */
export default function RoiTemplatePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [form, setForm] = useState<Form | null>(id ? null : toForm("", DEFAULT_ASSUMPTIONS));
  const [step, setStep] = useState<1 | 2>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (id) api.roiTemplate(id).then((t) => setForm(toForm(t.name, t.assumptions)), (e) => setError(e.message));
  }, [id]);

  if (!form) {
    return <main className="page narrow">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</main>;
  }

  const set = (key: keyof Form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const toggle = (key: Indicator) => {
    const on = form.indicators.includes(key);
    setForm({ ...form, indicators: INDICATORS.map((x) => x.key).filter((k) => (k === key ? !on : form.indicators.includes(k))) });
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    if (step === 1) return setStep(2); // the browser already checked step 1's required fields
    const assumptions: RoiAssumptions = {
      hourly_cost: num(form.hourly_cost), build_cost: num(form.build_cost), monthly_cost: num(form.monthly_cost),
      months: 12, hours_per_fte: num(form.hours_per_fte) ?? 168,
      currency: form.currency, indicators: form.indicators,
    };
    setBusy(true);
    setError(null);
    try {
      if (id) await api.updateRoiTemplate(id, form.name.trim(), assumptions);
      else await api.createRoiTemplate(form.name.trim(), assumptions);
      navigate("/roi");
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function remove() {
    if (!id || !confirm("Delete this ROI template? Projects that used it keep their own copy of its figures.")) return;
    try {
      await api.deleteRoiTemplate(id);
      navigate("/roi");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const field = (key: NumberField, label: string, hint: string, placeholder = "", optional = false) => (
    <div className={`field${optional ? " optional" : ""}`}>
      <label htmlFor={`rt-${key}`}>{label}</label>
      <input id={`rt-${key}`} type="number" min={0} step="any" inputMode="decimal" placeholder={placeholder}
             value={form[key]} onChange={set(key)} required={key === "hours_per_fte"} />
      <p className="after">{hint}</p>
    </div>
  );

  return (
    <main className="page narrow">
      <div className="page-head" style={{ gap: 12 }}>
        <Link to="/roi" className="back">← Back to ROI</Link>
        <h1>{id ? "ROI template" : "New ROI template"}</h1>
        <p>
          Your company's figures for working out what automating a process saves. Choose it for a project and its
          figures are copied there; the runs per month and time per run come from each project.
        </p>
      </div>

      <form className="card form roi-form" style={{ padding: 36 }} onSubmit={submit}>
        <div className="roi-wide"><WizardSteps step={step} /></div>

        {step === 1 ? (
          <>
            <div className="field roi-wide">
              <label htmlFor="rt-name">Name</label>
              <input id="rt-name" type="text" required maxLength={120} placeholder="e.g. Finance team" value={form.name}
                     onChange={set("name")} />
            </div>
            <div className="field">
              <label htmlFor="rt-currency">Currency</label>
              <select id="rt-currency" value={form.currency} onChange={set("currency")}>
                {CURRENCIES.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
              </select>
            </div>
            {field("hourly_cost", "Cost per hour of the people doing it", "Salary plus charges, per hour worked.", "e.g. 45")}
            {field("hours_per_fte", "Hours a month of a full-time person", "For the FTE freed: e.g. 168, or 220 under the CLT.")}
            {field("build_cost", "Cost to build an automation (optional)", "Paid once: development and testing. Empty: none.", "e.g. 8000", true)}
            {field("monthly_cost", "Cost to run it, per month (optional)", "Licences, infrastructure and upkeep. Empty: none.", "e.g. 300", true)}
          </>
        ) : (
          <>
            <div className="field roi-wide">
              <p className="field-title" id="rt-indicators">Indicators to report</p>
              <div className="roi-indicators" role="group" aria-labelledby="rt-indicators">
                {INDICATORS.map((x) => (
                  <label key={x.key} className="check">
                    <input type="checkbox" checked={form.indicators.includes(x.key)} onChange={() => toggle(x.key)} />
                    <span>{x.label}<span className="check-hint">{x.hint}</span></span>
                  </label>
                ))}
              </div>
            </div>
          </>
        )}

        {error && <p className="error roi-wide" role="alert">{error}</p>}
        <div className="actions roi-wide">
          {id && <button type="button" className="btn btn-danger-text" onClick={remove}>Delete</button>}
          {step === 1
            ? <Link to="/roi" className="btn btn-outline">Cancel</Link>
            : <button type="button" className="btn btn-outline" onClick={() => setStep(1)}>Back</button>}
          <button className="btn btn-dark" disabled={busy}>
            {step === 1 ? "Next" : busy ? "Saving…" : id ? "Save" : "Create ROI template"}
          </button>
        </div>
      </form>
    </main>
  );
}
