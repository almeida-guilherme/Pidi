import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import type { RunStatus, User } from "./api";
import { STATUS_LABELS } from "./format";

/** "pidı" with the accent as the dot of the i; a link when given `to`. */
export function Logo({ to, large, label = "Pidi home" }: { to?: string; large?: boolean; label?: string }) {
  const letters = <><span>pid</span><span className="logo-i">ı</span></>;
  const className = `logo${large ? " large" : ""}`;
  return to
    ? <Link to={to} className={className} aria-label={label}>{letters}</Link>
    : <span className={className} role="img" aria-label="Pidi">{letters}</span>;
}

/** Shown when a recording costs more credits than the person has. A native <dialog>: Esc closes it. */
export function NoCreditsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog ref={ref} className="modal" aria-labelledby="no-credits-title" onClose={onClose}
            onClick={(e) => { if (e.target === ref.current) onClose(); /* a click on the backdrop */ }}>
      <Logo large />
      <h2 id="no-credits-title">Oops… You don't have enough credits.</h2>
      <p>Get more and let's keep processing!</p>
      <div className="modal-actions">
        <button type="button" className="btn btn-outline" onClick={onClose}>Not now</button>
        <Link to="/credits" className="btn btn-dark" autoFocus>Buy credits</Link>
      </div>
    </dialog>
  );
}

/** Where a form in steps is: the current step black with its number in the page's colour, the others the other
way round, joined by a dashed line. */
export function WizardSteps({ step, total = 2 }: { step: number; total?: number }) {
  return (
    <ol className="wizard-steps" aria-label={`Step ${step} of ${total}`}>
      {Array.from({ length: total }, (_, i) => i + 1).flatMap((n) => [
        n > 1 && <li key={`line-${n}`} className="wizard-line" aria-hidden="true" />,
        <li key={n} className={`wizard-dot${n === step ? " active" : ""}`} aria-current={n === step ? "step" : undefined}>
          {n}
        </li>,
      ])}
    </ol>
  );
}

export function initials(user: User): string {
  const words = (user.name || user.email.split("@")[0]).split(/[\s._-]+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase() || "?";
}

export function StatusPill({ status }: { status: RunStatus }) {
  return <span className={`pill pill-${status}`}>{STATUS_LABELS[status]}</span>;
}

export function PlusIcon({ size = 16 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round">
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

export function UploadIcon({ size = 20 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13V4M6 8l4-4 4 4M4 14v2h12v-2" />
    </svg>
  );
}
