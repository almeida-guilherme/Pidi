# Open requirements

What is done and what is still missing, by when it matters.

## Before putting it on a server

- [x] **HTTPS and hosting.** `compose.yaml` runs the app behind Caddy, which handles HTTPS and large uploads; `deploy/LIGHTSAIL.md` is a step-by-step for one small server. The app runs as a single process (the job queue lives in it), with `data/` on a persistent disk.
- [ ] **Backups of `data/`** (your part, on each server). It holds the database (accounts, projects, encrypted API keys), every uploaded recording and every document. Keep `data/secret_key` (or set `PDD_SECRET_KEY`) with it: without it, everyone is logged out and the stored API keys can't be read.
- [ ] **Close sign-up** (your part). `PDD_ALLOW_SIGNUP=false` once your account exists (let others in with `python -m web.create_user`), or `PDD_SIGNUP_DOMAINS` for a company's addresses. Otherwise any Google account can create an account.
- [x] **Who pays for the AI.** By default (`PDD_BILLING=keys`) each person connects their own provider's key on the Account page, so the server's owner pays for nothing. `PDD_BILLING=credits` runs every project on the server's `OPENAI_API_KEY` and people pay with credits; then give that key its own OpenAI project with a monthly budget limit.

## Before more users

- [x] **Log in with Google only** (no passwords to guess, reset or confirm). Needs a Google OAuth client (`PDD_GOOGLE_CLIENT_ID`, `PDD_GOOGLE_CLIENT_SECRET`). Failed log-ins are limited per address.
  - [x] **A local login for self-hosting** (`PDD_LOCAL_LOGIN`): a secret link printed at startup, so trying the website doesn't need a Google OAuth client.
- [x] **Privacy and consent (LGPD).** Privacy notice at `/privacy`, accepted before the first project; each upload confirms the uploader may upload it; people download their data or delete their account from the Account page.
  - [ ] **Have the privacy notice reviewed** by someone who knows the LGPD before other people use a site, and fill in `PDD_OPERATOR_NAME` and `PDD_CONTACT_EMAIL`. The text is a starting point, not legal advice.
- [x] **Retention setting.** `PDD_RETENTION_DAYS` (default 30): the video, audio and frames the document doesn't use are deleted; the document stays.
- [x] **More than one project at a time.** Two workers by default, one project per person at a time, so people take turns. A real job queue (several servers) only if one server stops being enough.
- [x] **Monitoring.** Failed projects and server errors are emailed to `PDD_ALERT_EMAIL`; `/api/health` for an uptime monitor.
- [x] **People's own API keys** (`web/keys.py`): OpenAI, Anthropic, Gemini, or the server's Ollama (`PDD_OLLAMA_URL`). Keys are checked with the provider, stored encrypted and never shown again; a key can name its own model.
- [x] **Credits** (`PDD_BILLING=credits`, kept for a hosted site): charged at upload by the recording's length, given back only when deleted before processing or stopped at a cost limit. Bought with Asaas's checkout (Pix or card) and its webhook.
  - [ ] **Test in Asaas's sandbox on a server**, then switch to a production key.
  - [ ] **Refunds and chargebacks** don't take credits back yet (do it with `grant_credits` and a negative number).
  - [ ] **Terms of sale and invoices** (nota fiscal) before charging real money; Asaas can issue invoices.
- [x] **Costs.** Every project, redo, assessment and template reading records its cost by model (`costs.py`, `web/usage.py`); the Dashboard shows them by month and project, and New project estimates a recording's cost before uploading.
  - [ ] **Cheaper models where they hold up**: compare cost and quality per model with the evaluation before changing a default.
- [ ] **Company rules** (your part). Check with the employer before colleagues upload company or client recordings: the site asks each uploader to confirm, but can't check it.

## Product

- [x] **ROI analysis.** ROI templates hold a company's assumptions and indicators; every project gets an AI viability assessment and, with an ROI template, its savings, payback and ROI, on its page and in the document. Older projects get it from "Assess this project"; other languages are translated once per project.
- [x] **Automation spec.** Each finished project downloads its process as Markdown with screenshots (`spec.py`) for an agent that builds the automation.
  - [ ] **The builder** (a separate project): plans from the spec and writes the automation, in Python first, UiPath later, tried against a test environment and reviewed as a pull request.
- [x] **Review and redo.** On a finished project, people approve the steps or say what's wrong in each one; a redo has the AI fix those steps against the recording, keeps every version and can be undone (`revise.py`, `web/reviews.py`).
- [x] **Correcting a template's analysis.** On a template's page each part can be changed and undone; projects created afterwards follow it.
- [x] **Other AI providers** through LangChain (`providers.py`): OpenAI, Anthropic, Gemini and Ollama, with Whisper on the server when there's no OpenAI key.
  - [ ] **Real runs on Claude, Gemini and the server's Whisper**: tested offline only. Ollama runs, but small local models often refuse a redo's edits.
- [x] **Evaluation set** (`evaluation/`): cases made from finished projects, steps written again several times and scored against the expected ones.
  - [ ] **Approve the first cases** on their projects' review pages, then a first run to have a baseline.
  - [ ] **Steady step count.** The same recording gives 10–14 steps across runs; measure it with the evaluation, then fix the prompt.
- [ ] **A long recording** (30+ minutes) through the current pipeline on a cloud provider.
- [ ] **Table of contents page numbers** in template documents: Word fills them when the document is opened (it asks to update fields); LibreOffice shows the table of contents without them.

## Open source

- [x] MIT license, a README for newcomers, names from real recordings removed from the tests.
- [x] **An example recording** (`examples/`): an invented finance portal, recorded by a script.
  - [x] **The document Pidi writes from it** (`examples/invoice-approval-PDD.pdf`), with one redo.
- [ ] **The public repository**: one commit of the cleaned-up code, under the GitHub noreply address.
- [ ] **Demo video and LinkedIn post.**
