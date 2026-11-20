import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Purchase, type Site } from "../api";
import { useCredits } from "../App";
import { dateTime, reais } from "../format";
import { useSite } from "../site";

const plural = (n: number) => `${n} credit${n === 1 ? "" : "s"}`;
// After coming back from Asaas: how often, and for how long, to ask whether the payment was confirmed.
const POLL_MS = 3000;
const POLL_FOR_MS = 5 * 60 * 1000;

/** Buying credits: choose how many, pay on Asaas's page, come back here (/credits/<purchase>) to see it confirmed. */
export default function CreditsPage() {
  const site = useSite();
  const { balance, refresh } = useCredits();
  const { purchaseId } = useParams();
  const [buying, setBuying] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<Purchase[] | null>(null);
  const payments = site?.payments;

  useEffect(() => {
    api.purchases().then(setHistory, () => setHistory([]));
  }, [purchaseId]);

  async function buy(credits: number) {
    setBuying(credits);
    setError(null);
    try {
      const { link } = await api.buy(credits);
      window.location.assign(link); // Asaas's page; it sends the browser back to /credits/<purchase>
    } catch (err) {
      setError((err as Error).message);
      setBuying(null);
    }
  }

  return (
    <main className="page medium">
      <div className="page-head">
        <h1>Credits</h1>
        <p>
          Projects are paid with credits
          {site ? `: ${plural(site.credits.per_minute)} per started minute of video` : ""}. If a project fails, retrying it
          is free.
        </p>
      </div>

      {purchaseId && <Returned id={purchaseId} onPaid={() => { refresh(); api.purchases().then(setHistory); }} />}

      <section className="card" aria-labelledby="sec-buy">
        <div className="card-head">
          <p className="balance">
            <strong>{balance ?? "–"}</strong> credit{balance === 1 ? "" : "s"} left
          </p>
          <h2 id="sec-buy">Buy credits</h2>
          <p>Pay with Pix or card on Asaas's secure page. The credits are added as soon as the payment is confirmed.</p>
        </div>
        {payments?.sandbox && payments.configured && (
          <p className="notice">Test mode: payments go to Asaas's sandbox and no real money is charged.</p>
        )}
        {payments && !payments.configured ? (
          <p className="notice">Buying credits isn't set up on this site yet. Ask the site's administrator for credits.</p>
        ) : (
          payments && site && (
            <div className="pack-grid">
              {payments.suggested.map((n) => (
                <Pack key={n} credits={n} payments={payments} perMinute={site.credits.per_minute} buying={buying}
                      onBuy={buy} />
              ))}
              <Pack custom credits={Math.min(payments.max, Math.max(payments.min, 100))} payments={payments}
                    perMinute={site.credits.per_minute} buying={buying} onBuy={buy} />
            </div>
          )
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <p className="subtle small">
          Asaas handles the payment; Pidi never sees your card. See every change to your credits on the{" "}
          <Link to="/account#credits">Account page</Link>.
        </p>
      </section>

      {history && history.length > 0 && (
        <section className="card" aria-labelledby="sec-bought">
          <h2 id="sec-bought">Purchases</h2>
          <table className="ledger">
            <thead><tr><th scope="col">Paid</th><th scope="col">Credits</th><th scope="col">Price</th></tr></thead>
            <tbody>
              {history.map((p) => (
                <tr key={p.id}>
                  <td className="subtle">{dateTime(p.paid_at ?? p.created_at)}</td>
                  <td>{p.credits}</td>
                  <td>{reais(p.price_cents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

/** A pack card: a fixed number of credits, or (`custom`) a number the person chooses, with its price. */
function Pack({ payments, perMinute, buying, onBuy, credits, custom }: {
  payments: Site["payments"]; perMinute: number; buying: number | null; onBuy: (credits: number) => void;
  credits: number; custom?: boolean;
}) {
  const { min, max, price_cents: price } = payments;
  const [text, setText] = useState(String(credits));
  const amount = custom ? (/^\d+$/.test(text) ? Number(text) : NaN) : credits;
  const valid = amount >= min && amount <= max;
  const set = (n: number) => setText(String(Math.min(max, Math.max(min, n))));

  return (
    <div className={`pack${custom ? " pack-custom" : ""}`}>
      {custom ? (
        <div className="pack-stepper">
          <button type="button" className="qty-btn" aria-label="One credit less"
                  disabled={!valid || amount <= min} onClick={() => set(amount - 1)}>−</button>
          <input type="number" inputMode="numeric" className={`pack-credits${text.length > 3 ? " long" : ""}`}
                 aria-label="How many credits" title={`From ${min} to ${max}`} aria-invalid={!valid}
                 min={min} max={max} step={1} value={text} onChange={(e) => setText(e.target.value)} />
          <button type="button" className="qty-btn" aria-label="One credit more"
                  disabled={valid && amount >= max} onClick={() => set(Number.isNaN(amount) ? min : amount + 1)}>+</button>
        </div>
      ) : (
        <span className="pack-credits">{credits}</span>
      )}
      <span className="subtle">{custom ? "credits, you choose" : "credits"}</span>
      <span className="pack-price" aria-live={custom ? "polite" : undefined}>{valid ? reais(amount * price) : "–"}</span>
      <span className="subtle small">
        {valid ? `${reais(price)} per credit · about ${Math.floor(amount / perMinute)} min of video`
               : `Choose a number from ${min} to ${max}.`}
      </span>
      <button type="button" className="btn btn-dark btn-block" disabled={!valid || buying !== null}
              onClick={() => onBuy(amount)}>
        {buying === amount ? "Opening Asaas…" : valid ? `Buy ${amount}` : "Buy"}
      </button>
    </div>
  );
}

/** Back from Asaas: waits for the webhook to confirm the payment (Pix and cards take a few seconds). */
function Returned({ id, onPaid }: { id: string; onPaid: () => void }) {
  const [params] = useSearchParams();
  const left = params.get("result"); // success, canceled or expired: how the person left Asaas's page
  const [purchase, setPurchase] = useState<Purchase | null>(null);
  const [gaveUp, setGaveUp] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const started = Date.now();
    async function check() {
      try {
        const p = await api.purchase(id);
        if (stop) return;
        setPurchase(p);
        if (p.status === "paid") return onPaid();
        if (p.status !== "pending" || left === "canceled" || left === "expired") return;
      } catch (err) {
        if (!stop) setError((err as Error).message);
        return;
      }
      if (Date.now() - started > POLL_FOR_MS) setGaveUp(true);
      else timer = window.setTimeout(check, POLL_MS);
    }
    check();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
    // Not on onPaid, which is a new function on every render of the page.
  }, [id, left]);

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!purchase) return null;
  const what = `${plural(purchase.credits)} for ${reais(purchase.price_cents)}`;
  let tone = "notice", text: string;
  if (purchase.status === "paid") {
    tone = "notice notice-ok-box";
    text = `Payment confirmed: ${plural(purchase.credits)} added.`;
  } else if (purchase.status === "expired" || left === "expired") {
    text = `The payment page for ${what} expired before it was paid. Nothing was charged; buy again to try once more.`;
  } else if (purchase.status === "canceled" || left === "canceled") {
    text = `You left the payment for ${what}. Nothing was charged.`;
  } else if (gaveUp) {
    text = `The payment for ${what} isn't confirmed yet. If you paid, the credits are added as soon as Asaas confirms it; this page doesn't need to stay open.`;
  } else {
    text = `Waiting for Asaas to confirm the payment for ${what}…`;
  }
  return <p className={tone} role="status" aria-live="polite">{text}</p>;
}
