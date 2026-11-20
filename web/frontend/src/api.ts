// Typed calls to the FastAPI backend (web/). The session is an httpOnly cookie, so requests only need
// to be same-origin; nothing is stored in the browser by this code.

export type User = { id: number; email: string; name: string; privacy_accepted: boolean };

/** Public facts about the site, for the privacy notice and forms. */
export type Site = {
  operator: string;
  contact: string;
  retention_days: number;
  privacy_version: string;
  signup: boolean;
  google: boolean; // Log in with Google is set up
  local_login: boolean; // PDD_LOCAL_LOGIN: a log-in link in the server's output
  languages: Language[]; // offered for Pidi's template; any other can be typed by name
  // How projects are paid for: "keys", each person's own OpenAI key; "credits", the server's key and credits.
  billing: "keys" | "credits";
  server_key: boolean; // with "keys": people without a key of their own can use the server's
  credits: { per_minute: number; translation: number; signup: number }; // what projects cost, what new accounts get
  // Buying credits through Asaas; sandbox: its test environment, no real money.
  payments: { configured: boolean; sandbox: boolean; price_cents: number; min: number; max: number; suggested: number[] };
};

/** What the person's AI provider has cost them, estimated (web/usage.py). */
export type ProjectCost = {
  run_id: string; name: string; created_at: string; minutes: number | null;
  providers: string[]; models: string[];
  by_kind: Record<string, number>; // project, redo, assess
  total: number; per_minute: number | null;
};
export type Costs = {
  this_month: number; last_month: number; total: number;
  months: { month: string; by_provider: Record<string, number> }[]; // the last twelve, oldest first
  by_model: Record<string, number>; by_kind: Record<string, number>;
  projects: ProjectCost[]; templates: number; unpriced: string[]; per_minute: number | null;
};

/** Roughly what a recording will cost on a key (web/usage.py). */
export type Estimate = {
  low: number | null; mid: number | null; high: number | null;
  basis: "history" | "typical" | "free" | "unknown"; projects: number; unpriced: string[];
};

/** A person's own AI provider key (billing "keys"); only a hint of it is ever shown. */
export type ApiKey = {
  id: string; provider: string; name: string; hint: string; // hint: "" for Ollama, which has no key
  model: string | null; // instead of the provider's default models
  created_at: string;
};
/** A provider that can be connected on this server (providers.py). */
export type Provider = { id: string; label: string; needs_key: boolean; keys_url: string; models: string[] };
export type Keys = { keys: ApiKey[]; server_key: boolean; providers: Provider[] };

/** A language of Pidi's template; `translated` ones are translated by the model the first time they're used. */
export type Language = { code: string; name: string; translated: boolean };

/** A change to the balance: credits given (grant), paid for a project (charge) or given back (refund). */
export type CreditEntry = {
  amount: number;
  kind: "grant" | "purchase" | "charge" | "refund";
  note: string | null;
  run_id: string | null;
  created_at: string;
};
export type Credits = { balance: number; entries: CreditEntry[] }; // entries newest first
export type Quote = { credits: number; balance: number };
/** Credits bought; they are added when Asaas confirms the payment. */
export type Purchase = {
  id: string;
  credits: number;
  price_cents: number;
  status: "pending" | "paid" | "canceled" | "expired";
  created_at: string;
  paid_at: string | null;
};

export type RunStatus = "queued" | "running" | "done" | "failed";

export type RunSummary = {
  steps: number | null;
  screenshots: number | null;
  applications: string[];
  recorded_seconds: number | null;
  warnings: string[];
  to_complete: number;
};

export type Run = {
  id: string;
  video_name: string;
  language: string | null; // of Pidi's template (a code, or a typed language name); null with a company template
  status: RunStatus;
  stage: string | null;
  message: string | null;
  error: string | null;
  process_name: string | null;
  name: string | null;
  template_name: string | null;
  credits: number | null; // what it was charged
  refunded: boolean; // those credits were given back
  cost_usd: number | null; // what it has cost on the AI provider so far, estimated (only on a single project)
  stages: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  recording_deleted_at: string | null;
  summary: RunSummary | null;
  roi: Roi | null; // only on a single project (api.run)
};

export type Currency = "BRL" | "USD" | "EUR";

export type Indicator = "hours_saved" | "savings" | "net_savings" | "payback" | "roi" | "fte";

/** A company's ROI assumptions: what an ROI template holds (roi.RoiAssumptions). */
export type RoiAssumptions = {
  hourly_cost: number | null;
  build_cost: number | null;
  monthly_cost: number | null;
  months: number;
  hours_per_fte: number;
  currency: Currency;
  indicators: Indicator[];
};

/** A project's ROI figures (roi.RoiInputs): its ROI template's copy and the process's own; null where not given. */
export type RoiInputs = RoiAssumptions & {
  template_name: string | null;
  runs_per_month: number | null; // null: as the narration says, if it does
  minutes_per_run: number | null; // null: the AI's estimate, or the recorded time
};

export type RoiResult = {
  runs_per_month: number | null;
  runs_source: "given" | "narration" | null;
  minutes_per_run: number | null;
  minutes_source: "given" | "estimated" | "recorded" | null;
  hours_saved: number | null;
  savings: number | null;
  net_savings: number | null;
  payback_months: number | null;
  pays_back: boolean;
  roi_percent: number | null;
  fte: number | null;
};

/** Whether the process is worth automating, from the AI (assess.py). */
export type Assessment = {
  verdict: "good" | "partial" | "poor";
  summary: string;
  strengths: string[];
  blockers: string[];
  recommendations: string[];
  handling_minutes: number | null;
  handling_reasoning: string;
  runs_per_month: number | null;
  volume_quote: string | null;
};

export type Roi = {
  inputs: RoiInputs;
  result: RoiResult;
  recorded_seconds: number | null;
  assessment: Assessment | null;
};

export type RoiTemplate = {
  id: string;
  name: string;
  assumptions: RoiAssumptions;
  created_at: string;
  updated_at: string;
};

/** automation.md as the project page shows it (spec.py). */
export type SpecPreview = { markdown: string; screenshots: number; open_questions: number };

export type Step = {
  number: number;
  time: string;
  instruction: string;
  short_label: string;
  application: string;
  decision: string;
  duration_seconds: number | null;
  image_url: string | null;
};

/** What can be wrong with a step (web/reviews.py). */
export type StepIssue = "wording" | "missing_after" | "split" | "merge_next" | "screenshot" | "name" | "other";
export type StepNote = { number: number; issue: StepIssue; note: string };
export type Feedback = { general: string; steps: StepNote[] };
/** A redo (revise.py): the version it made, the notes it came from, what came of each note. */
export type Version = {
  number: number;
  created_at: string;
  notes: Feedback;
  answers: { label: string; outcome: "fixed" | "not_in_recording" | "unclear"; explanation: string }[];
  changed: number[]; // steps new or changed in this version
};
export type Review = {
  status: "pending" | "changes_requested" | "redoing" | "approved";
  feedback: Feedback;
  updated_at: string | null;
  approved_at: string | null;
  version: number; // the document's current version
  versions: Version[]; // the redos, oldest first
  redo_started_at: string | null;
  redo_error: string | null; // why the last redo failed
  redos_left: number | null; // null: no limit
};

export type ProcessModel = {
  process_name: string;
  project_proposal: string;
  as_is: Step[];
  inputs: { name: string; kind: "structured" | "semi_structured" | "unstructured"; source: string }[];
  requirements: { category: string; description: string }[];
  business_exceptions: ExceptionItem[];
  system_exceptions: ExceptionItem[];
};

export type Template = { id: string; name: string; language: string; created_at: string };

export type TemplateAction = "keep" | "remove" | "write" | "table_rows" | "table_answers" | "diagram" | "steps";

export type TemplateBlock = {
  index: number; kind: string; text: string; action: TemplateAction; purpose: string; reason: string;
  editable: boolean; // not where the steps or diagram are written
  original: { action: TemplateAction; purpose: string } | null; // the analysis's decision, when the author changed it
};

export type TemplateDetail = Template & {
  missing_label: string;
  placeholders: { text: string; meaning: string }[];
  step_label: string;
  blocks: TemplateBlock[];
};

export type ExceptionItem = { name: string; action: string; parameters: string; action_to_be_taken: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
  return (response.status === 204 ? undefined : await response.json()) as T;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    // FastAPI validation errors are a list; show the first one's message.
    if (Array.isArray(body.detail)) return String(body.detail[0]?.msg ?? "Invalid input.").replace(/^Value error, /, "");
    if (body.detail) return String(body.detail);
  } catch {
    /* not JSON */
  }
  return `Request failed (${response.status}).`;
}

const json = (body: unknown, method = "POST"): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  me: () => request<User>("/api/auth/me"),
  /** A full-page address: the server sends the browser to Google and, once logged in, back to `next`. */
  googleLoginUrl: (next: string) => `/api/auth/google?${new URLSearchParams({ next })}`,
  acceptPrivacy: () => request<User>("/api/auth/privacy", { method: "POST" }),
  site: () => request<Site>("/api/site"),
  exportUrl: "/api/account/export",
  deleteAccount: (email: string) => request<void>("/api/account", json({ email }, "DELETE")),
  updateProfile: (name: string) => request<User>("/api/auth/me", json({ name }, "PATCH")),
  credits: () => request<Credits>("/api/credits"),
  keys: () => request<Keys>("/api/keys"),
  costs: () => request<Costs>("/api/costs"),
  estimate: (seconds: number, apiKeyId: string | null) => {
    const params = new URLSearchParams({ seconds: String(seconds) });
    if (apiKeyId) params.set("api_key_id", apiKeyId);
    return request<Estimate>(`/api/costs/estimate?${params}`);
  },
  addKey: (provider: string, name: string, key: string, model: string) =>
    request<ApiKey>("/api/keys", json({ provider, name, key, model })),
  removeKey: (id: string) => request<void>(`/api/keys/${id}`, { method: "DELETE" }),
  /** The price of a recording of `seconds`; `language` null for a company template. */
  quote: (seconds: number, language: string | null) => {
    const params = new URLSearchParams({ seconds: String(seconds) });
    if (language !== null) params.set("language", language);
    return request<Quote>(`/api/credits/quote?${params}`);
  },
  /** Starts paying for a pack; returns Asaas's page to send the browser to. */
  buy: (credits: number) => request<{ id: string; link: string }>("/api/payments", json({ credits })),
  purchase: (id: string) => request<Purchase>(`/api/payments/${id}`),
  purchases: () => request<Purchase[]>("/api/payments"),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  runs: () => request<Run[]>("/api/runs"),
  run: (id: string) => request<Run>(`/api/runs/${id}`),
  model: (id: string) => request<ProcessModel>(`/api/runs/${id}/model`),
  review: (id: string) => request<Review>(`/api/runs/${id}/review`),
  saveFeedback: (id: string, feedback: Feedback) => request<Review>(`/api/runs/${id}/review/feedback`, json(feedback, "PUT")),
  approve: (id: string) => request<Review>(`/api/runs/${id}/review/approve`, { method: "POST" }),
  reopen: (id: string) => request<Review>(`/api/runs/${id}/review/reopen`, { method: "POST" }),
  redo: (id: string) => request<Review>(`/api/runs/${id}/review/redo`, { method: "POST" }),
  undoRedo: (id: string) => request<Review>(`/api/runs/${id}/review/undo`, { method: "POST" }),
  retry: (id: string) => request<Run>(`/api/runs/${id}/retry`, { method: "POST" }),
  assess: (id: string) => request<Run>(`/api/runs/${id}/assess`, { method: "POST" }),
  saveRoi: (id: string, inputs: RoiInputs) => request<Run>(`/api/runs/${id}/roi`, json(inputs, "PUT")),
  roiTemplates: () => request<RoiTemplate[]>("/api/roi-templates"),
  roiTemplate: (id: string) => request<RoiTemplate>(`/api/roi-templates/${id}`),
  createRoiTemplate: (name: string, assumptions: RoiAssumptions) =>
    request<RoiTemplate>("/api/roi-templates", json({ name, assumptions })),
  updateRoiTemplate: (id: string, name: string, assumptions: RoiAssumptions) =>
    request<RoiTemplate>(`/api/roi-templates/${id}`, json({ name, assumptions }, "PUT")),
  deleteRoiTemplate: (id: string) => request<void>(`/api/roi-templates/${id}`, { method: "DELETE" }),
  remove: (id: string) => request<void>(`/api/runs/${id}`, { method: "DELETE" }),
  documentUrl: (id: string) => `/api/runs/${id}/document`,
  automationUrl: (id: string) => `/api/runs/${id}/automation`,
  automationPreview: (id: string) => request<SpecPreview>(`/api/runs/${id}/automation/preview`),
  templates: () => request<Template[]>("/api/templates"),
  template: (id: string) => request<TemplateDetail>(`/api/templates/${id}`),
  changeTemplateBlock: (id: string, index: number, action: TemplateAction, purpose: string) =>
    request<TemplateDetail>(`/api/templates/${id}/blocks/${index}`, json({ action, purpose }, "PUT")),
  removeTemplate: (id: string) => request<void>(`/api/templates/${id}`, { method: "DELETE" }),
  uploadTemplate: (file: File, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    return request<TemplateDetail>("/api/templates", { method: "POST", body: form, signal });
  },
};

/** Uploads a recording with progress (fetch can't report upload progress, XMLHttpRequest can). */
export type NewProject = {
  name: string; templateId: string | null; language: string; roiTemplateId: string | null;
  apiKeyId?: string | null; // with billing "keys": the key it runs on; null: the server's
};

export function uploadRecording(file: File, project: NewProject, onProgress: (fraction: number) => void): Promise<Run> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/runs");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body: { detail?: unknown } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status === 201) resolve(body as Run);
      else reject(new ApiError(xhr.status, typeof body.detail === "string" ? body.detail : `Upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new ApiError(0, "The upload was interrupted. Check your connection and try again."));
    const form = new FormData();
    form.append("file", file);
    form.append("consent", "true"); // the form only submits once the uploader ticked the consent box
    if (project.name.trim()) form.append("name", project.name.trim());
    if (project.templateId) form.append("template_id", project.templateId);
    else form.append("language", project.language);
    if (project.roiTemplateId) form.append("roi_template_id", project.roiTemplateId);
    if (project.apiKeyId) form.append("api_key_id", project.apiKeyId);
    xhr.send(form);
  });
}
