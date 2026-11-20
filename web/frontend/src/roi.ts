import type { Assessment, Currency, Indicator, RoiAssumptions, RoiInputs, RoiResult } from "./api";

export const CURRENCIES: { code: Currency; label: string; locale: string }[] = [
  { code: "BRL", label: "R$ (real)", locale: "pt-BR" },
  { code: "USD", label: "US$ (dollar)", locale: "en-US" },
  { code: "EUR", label: "€ (euro)", locale: "de-DE" },
];

export const INDICATORS: { key: Indicator; label: string; hint: string }[] = [
  { key: "hours_saved", label: "Hours saved / month", hint: "Runs per month × time per run" },
  { key: "savings", label: "Savings / month", hint: "Hours saved × cost per hour" },
  { key: "net_savings", label: "Net savings / month", hint: "Savings minus the automation's monthly cost" },
  { key: "payback", label: "Payback", hint: "Months until the automation pays for itself (calculated)" },
  { key: "roi", label: "ROI", hint: "Return in the first 12 months, after the build cost" },
  { key: "fte", label: "FTE freed", hint: "Full-time people's work the automation takes over" },
];

export const DEFAULT_ASSUMPTIONS: RoiAssumptions = {
  hourly_cost: null, build_cost: null, monthly_cost: null, months: 12, hours_per_fte: 168, currency: "BRL",
  indicators: ["hours_saved", "savings", "net_savings", "payback", "roi"],
};

export const VERDICTS: Record<Assessment["verdict"], { label: string; tone: string }> = {
  good: { label: "Good candidate", tone: "good" },
  partial: { label: "Partly automatable", tone: "partial" },
  poor: { label: "Not a good candidate", tone: "poor" },
};

/** Money and numbers written the way the currency's country writes them. */
export function formatters(currency: Currency) {
  const { locale } = CURRENCIES.find((c) => c.code === currency)!;
  return {
    money: (v: number | null) => (v === null ? "–" : new Intl.NumberFormat(locale, { style: "currency", currency }).format(v)),
    number: (v: number, digits = 1) =>
      new Intl.NumberFormat(locale, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v),
  };
}

export function minutesText(minutes: number): string {
  const total = Math.round(minutes * 60);
  if (total >= 3600) return `${Math.floor(total / 3600)} h ${String(Math.floor((total % 3600) / 60)).padStart(2, "0")} min`;
  if (total < 60) return `${total} s`;
  return `${Math.floor(total / 60)} min${total % 60 ? ` ${String(total % 60).padStart(2, "0")} s` : ""}`;
}

/** An indicator's value as shown. */
export function indicatorValue(key: Indicator, r: RoiResult, i: RoiInputs): string {
  const { money, number } = formatters(i.currency);
  switch (key) {
    case "hours_saved": return r.hours_saved === null ? "–" : `${number(r.hours_saved)} h`;
    case "savings": return money(r.savings);
    case "net_savings": return money(r.net_savings);
    case "payback":
      if (r.net_savings !== null && !r.pays_back) return "Never";
      if (r.payback_months === 0) return "Immediately";
      return r.payback_months === null ? "–" : `${number(r.payback_months)} months`;
    case "roi": return r.roi_percent === null ? "–" : `${number(r.roi_percent, 0)}%`;
    case "fte": return r.fte === null ? "–" : number(r.fte, 2);
  }
}

export function isNegative(key: Indicator, r: RoiResult): boolean {
  return (key === "net_savings" && (r.net_savings ?? 0) < 0) || (key === "roi" && (r.roi_percent ?? 0) < 0);
}

export function indicatorLabel(key: Indicator, i: RoiInputs | RoiAssumptions): string {
  return key === "roi" ? `ROI in ${i.months} months` : INDICATORS.find((x) => x.key === key)!.label;
}
