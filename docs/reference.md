# Pidi reference

Turns a screen recording of someone doing a task (optionally narrated) into a **Process Design Document (PDD)** in Word format, the document RPA teams write before automating a process: introduction, flowchart, numbered steps with screenshots, and business/system exceptions.

## Setup

System tools:

```bash
sudo apt install ffmpeg graphviz   # graphviz is only needed for the flowchart
```

Python (3.12):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "OPENAI_API_KEY=sk-..." > .env
```

## Usage

```bash
.venv/bin/python main.py path/to/recording.mp4
```

Each run creates its own folder under `runs/`:

```
runs/recording-20261013-184500/
  frames/F001.jpg ...     screenshots kept from the video
  frames.json             their ids and timestamps
  audio/chunk_000.mp3 ... audio split for transcription
  captions.json           what the model saw in each frame
  transcript.json         the narration, with timestamps
  process_model.json      the process model the document is rendered from
  name_checks.json        similar names re-read at full resolution (see below)
  template.docx, template_analysis.json, template_fill.json   the template and what was written into it
  report.json             counts, coverage checks and cost (see below)
  output/PDD_final.docx
```

The document is written into **Pidi's template**, in the language chosen with `--language` (e.g. `en`, the default, `pt-BR`, `es`, or any language's name such as `Tagalog`) or on the website's new-project form. English (`templates/pidi.en.docx`) and Brazilian Portuguese (`templates/pidi.docx`) are translated by hand; any other language is translated by the model from the English one the first time it is used (`pidi_template.py`: every piece of fixed text in one call, about a cent), and kept in `templates/translated/` (on the website, `data/pidi_templates/`) so it isn't translated again. The template is: cover, revision history, introduction, impacted teams, overview (summary, systems, requirements, inputs, outputs), flowchart, automation scope and metrics, the steps (each with its application, recorded time and screenshot, grouped in numbered phases), exceptions and business rules. Its analysis (`templates/pidi.analysis.json`, see [Company templates](#company-templates)) was checked by hand and ships with it, so it isn't analyzed on every run; after editing the template, update that file too (the indexes refer to the template's blocks; `describe_template` in `template.py` lists them). The two languages have the same blocks, translated, so they share one analysis apart from the placeholders, labels and step numbering ("S1" / "P1"); keep them in step. A company template is always written in its own language. `--standard-layout` writes the older built-in layout (`render.py`, in English) instead.

Options: `--caption-width` (width frames are shrunk to before captioning, default 1366; OpenAI scales images to a 768 px short side, so larger gains nothing, and smaller misreads file names and column headers), `--window` (observations per refinement call, default 30), `--sample-every` (seconds between sampled frames, default 0.5), `--change-threshold` (share of changed pixels that counts as a new screen state, default 0.001).

To redo only the structuring and the document (e.g. after changing a prompt), reuse an earlier run's frames, captions and transcript, which are the expensive part, in a new run folder:

```bash
.venv/bin/python main.py path/to/recording.mp4 --from-run runs/recording-20261013-184500
```

To continue a run that stopped (network error, rate limit, Ctrl+C) in its own folder, without redoing what it finished:

```bash
.venv/bin/python main.py path/to/recording.mp4 --resume runs/recording-20261013-184500
```

Each stage saves its result in the run folder and is skipped when that file exists; captions are saved after every frame (frames are captioned several at a time, `PDD_CAPTION_CONCURRENCY`, default 6; a 40-minute recording has over a thousand frames). Resuming a finished run only rebuilds the document. Delete `process_model.json` and `name_checks.json` from the folder to redo the structuring too.

Models are set with environment variables: `PDD_MODEL` for captioning frames (default `gpt-4.1-mini`, which read screen text best in tests and is billed far fewer tokens per image than `gpt-4o-mini`; every frame is an image, so this is where most of the cost is) and `PDD_STRUCTURE_MODEL` for structuring (default `gpt-4.1` at temperature 0; text only, so a strong model costs little here; `gpt-5` needs a verified OpenAI organization). The cost in the run report comes from the price table in `costs.py` plus Whisper's per-minute price; the report warns about models missing from that table, so add new models there.

## Company templates

To write the document into the PDD format a company already uses instead of Pidi's, pass its Word template:

```bash
.venv/bin/python main.py path/to/recording.mp4 --template path/to/PDD_Template.docx
```

The template is read once per run (and once per upload on the website), by `template.py`:

- **Analysis**: the model sees the template block by block (paragraphs with their styles, tables row by row, images, page headers) and decides for each one: keep (fixed text such as the introduction or a generic list of requirements), remove (instructions to the author like "<Lista de todos os sistemas>", and leftovers of the process the template was last used for: example steps, screenshots, systems, people, sample rows), write (a paragraph written for each process), fill a table's rows, answer a question table (e.g. metrics), or put the flowchart there. It also lists the short placeholders ("<Cliente>", "<Nome do Processo>"), finds how the example step is written ("P1 - ...", grouped under headings) and the template's language. `python template.py PDD_Template.docx` prints what it decided.
- **Fill**: after the steps are worked out (in the template's language and in the wording of its example step), one call writes every part marked to fill from the process model and the narration: tables (systems, inputs, exceptions, revision history...), metrics answers, scope, captions and placeholder values.
- **Render** (`render_template.py`): the template's own file is the starting point, so its cover, logo, styles, headers, numbering and fixed text stay exactly as they are. Example blocks are removed, tables keep their header and the formatting of their first row, steps take the format of the example step (and of its details line, e.g. "Aplicação: SAP  Tempo gravado: 00:12", when there is one), the flowchart replaces the example diagram or the box marking its place, and the table of contents is rebuilt (Word fills in the page numbers when the document is opened).

Whatever the recording doesn't say (contacts, versions, login modules, volumes, risk...) is never invented: it is written as the template's own "to fill" marker (e.g. "[A preencher]"), or the placeholder is left, highlighted in yellow so the author sees what to complete. The run report counts them (`missing_marks`).

The run folder keeps its own `template.docx`, `template_analysis.json` and `template_fill.json`, so `--resume` and `--from-run` work as usual. Deleting `template_fill.json` rewrites the template's sections; deleting `template_analysis.json` too analyzes the template again.

## How it works

The pipeline is a LangGraph graph (`pipeline.py`). Every stage reads and writes a shared state whose central piece is the **process model** (`ProcessModel` in `schemas.py`): steps with their screenshots, application, decision and recorded duration; the process inputs (structured, semi-structured or unstructured); key names; business rules and requirements; exceptions. The inputs are the recording and a template (Pidi's or a company's); the output is the .docx. A run folder may also hold the author's ROI figures (`roi.json`, see below); the document then gets an ROI section.

```
START ─┬─ analyze_template ───────┬─┬─ extract_frames ─ caption ─ transcribe ─┬─ structure ─ verify_names ─┬─ fill_template ───────┬─ render ─ END
       └─ (template analyzed) ────┘ └─ reuse_run (--from-run) ─────────────────┘                            └─ (--standard-layout) ─┘
```

1. **Frames** (`create_video.py`): samples the video and keeps a frame whenever enough pixels differ from the last kept one and the screen has settled, plus the final state of a screen right before it changes (e.g. a filled form before Save). Scrolling or animations get one frame every few seconds. Each frame gets an id (`F001`, `F002`, ...).
2. **Captions**: each frame is described by the vision model (screen, action, visible text).
3. **Transcription** (`transcribe.py`): the audio is cut into 10-minute chunks (Whisper accepts at most 25 MB per file) and the timestamps are shifted back to video time. Videos without audio skip this step.
4. **Structuring** (`structure.py`), in two passes:
   - *Outline*: one call sees the whole recording (a compact view of every frame plus the full narration) and decides the complete ordered list of steps (each with its application, any decision, and the first and last frame showing it), the process inputs, the business rules and requirements, the exceptions, and the correct spelling of key names (files, sites, views) with the misreadings it saw. The narration is the main source for what the steps are; explanations become requirements rather than steps. Deciding the steps once avoids duplicates and gaps between windows.
   - *Refine*: steps are grouped so each group's frames fit in one window, and one call per group gets the detailed captions to make each instruction specific and pick its screenshot. It can't add, drop or reorder steps.

   Every list the model returns has a maximum length that the API enforces while generating: on a 41-minute meeting, the outline otherwise listed misreadings of a window title until it ran out of output tokens and failed. Long recordings also keep all their frames (up to 2000).

   Code numbers the steps, takes each step's start and duration from its frames, maps ids to image files (so the model never writes file paths), and replaces misreadings of key names. Only variants a few characters away from the name are replaced, since the model sometimes lists different things as variants.
5. **Name check** (`verify_names.py`): captions see frames scaled down, where small text like a title bar is misread differently from frame to frame (e.g. "5.5.26" read as "5.3.26" in half the frames), and the text-only model can't tell which reading is right. For each pair of key names a character or two apart, a few frames whose captions mention either are cut into overlapping full-resolution tiles and the vision model is asked which name actually appears. If only one ever does, the other is replaced by it. If both do (e.g. two dated files listed side by side), they are different things and are kept.
6. **Fill and document** (`template.py`, `render_template.py`, see [Company templates](#company-templates)): one call writes the template's sections from the process model, the narration and facts the code knows (recording file, recorded duration, decision points); python-docx writes them into the template with the flowchart (long processes are laid out in columns joined by connector circles; decisions are diamonds) and the steps with screenshots. With `--standard-layout`, `render.py` builds the older built-in layout instead.

## Website

Pidi, the website: a FastAPI backend (`web/`) and a React front end (`web/frontend/`) let people upload a recording in the browser as a project, follow its progress and download the document. Each user has an account and only sees their own projects, templates and credits. The pages follow the Pidi design (landing page, log in / create account, projects, new project, account); fonts are bundled, so the site loads nothing from other servers.

Build the front end once (Node 22), then start the server; it serves both the API and the pages:

```bash
cd web/frontend && npm ci && npm run build && cd ../..
.venv/bin/uvicorn web.app:create_app --factory --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and log in with Google, which needs an OAuth client in `.env` first (see Accounts below). For development with live reload, run the same uvicorn command with `--reload` and, in another terminal, `cd web/frontend && npm run dev` (open http://localhost:5173; Vite forwards `/api` to port 8000).

- **Processing**: uploads go into a queue kept in the database and are processed by background threads in the server (`PDD_WORKERS`, default 2), taking turns between people: someone with `PDD_RUNS_PER_USER` (default 1) projects in progress waits, so one person's queue doesn't hold up everyone else. A run interrupted by a restart continues from its last finished stage, and a failed run can be retried from the page.
- **Storage**: everything is in `data/` (`PDD_DATA_DIR`): the SQLite database (`PDD_DATABASE_URL` to use another), and one folder per run with the uploaded recording and the same files as a command-line run.
- **Accounts**: people log in with Google (`web/google.py`, OpenID Connect); the first log-in creates the account. Pidi keeps the Google account's id, email and name, never a password. The session is an httpOnly, SameSite=Lax cookie signed with `PDD_SECRET_KEY` (generated into `data/secret_key` if unset), valid for `PDD_SESSION_DAYS` (default 7).
  - Set up: Google Cloud Console → APIs & Services → Credentials → *Create credentials → OAuth client ID* (Web application), with the authorized redirect URI `<site>/api/auth/google/callback` (locally `http://127.0.0.1:8000/api/auth/google/callback`). Put its id and secret in `PDD_GOOGLE_CLIENT_ID` and `PDD_GOOGLE_CLIENT_SECRET`. On a server, `PDD_PUBLIC_URL` gives the site's address for the redirect.
  - `PDD_SIGNUP_DOMAINS=company.com` limits accounts to company addresses (Google then offers only those accounts).
  - `PDD_ALLOW_SIGNUP=false` closes sign-up; let someone in with `.venv/bin/python -m web.create_user someone@company.com`. Accounts from the email-and-password version are joined to the Google account of their email on its first log-in.
  - Failed log-ins are limited per address (`web/ratelimit.py`).
  - **Local login** (`PDD_LOCAL_LOGIN=true`), for running Pidi on your own machine without a Google OAuth client: at startup the server prints a secret link (`/api/auth/local?token=...`, a new one at every start) that logs in as `PDD_LOCAL_EMAIL` (default `owner@pidi.local`), making the account the first time. Anyone with the link, or the server's output, can log in, so leave it off on a shared server.
- **Paying for projects** (`PDD_BILLING`): `keys` (the default) or `credits`.
  - **Own keys** (`web/keys.py`, `providers.py`): each person connects their own AI provider on the Account page: OpenAI, Anthropic (Claude), Google (Gemini), or the server's Ollama when `PDD_OLLAMA_URL` is set (no key; only the server owner sets where it runs). A key can name the model to use instead of the provider's defaults (`PDD_*_MODEL` settings, see `deploy/env.example`); a model must read images. All providers go through LangChain (Claude through Anthropic's own SDK underneath, with its structured outputs). The narration is read by OpenAI's Whisper with an OpenAI key, and otherwise by Whisper on the server (`faster-whisper`, `PDD_WHISPER_MODEL`, downloaded on first use). A key is checked with its provider (listing models, free), stored encrypted with a key derived from `PDD_SECRET_KEY` and never shown again (only `sk-…WXYZ`). Projects, template reading and "Assess this project" run on it, so OpenAI bills them; there's no price and no credits. A project whose key was removed fails with a message saying so. `PDD_ALLOW_SERVER_KEY=true` lets people without a key run on the server's `OPENAI_API_KEY`. Anyone with both the database and the secret can read the keys: keep `data/` private and its backups encrypted.
- **Credits** (`web/credits.py`, with `PDD_BILLING=credits`): every project runs on the server's `OPENAI_API_KEY` (required), so the site keeps nobody's key; people pay with credits. The pages about credits (the Credits page, the balance in the header, the price on New project) show only in this mode.
  - A project is charged when it's uploaded, by the recording's length: `PDD_CREDITS_PER_MINUTE` (default 1) per started minute, plus 1 the first time Pidi's template is translated into a language. The new-project page shows the price before uploading (the browser reads the video's length; the server measures it again). An upload that costs more than the balance is refused before anything is spent.
  - Credits are given back only when a project is deleted before it was processed, or when it stops at its cost limit (`PDD_MAX_COST_PER_CREDIT_USD`, default $0.15 per credit, kept below what a credit brings in after fees; a run costs about $0.06 per minute). Once processing started OpenAI was paid, so deleting a failed project gives nothing back; retrying it is free while its recording is kept.
  - New accounts get `PDD_SIGNUP_CREDITS` (default 10). Give more, or take some back, with `.venv/bin/python -m web.grant_credits someone@company.com 30 "note"`; without a number it shows the balance. The Account page shows the balance and its history.
  - Analyzing a company template runs on the server's key too and costs no credits; each person can upload 20 templates a day.
- **Buying credits** (`web/payments.py`, `web/asaas.py`): on the Credits page people choose how many credits (from `PDD_CREDIT_MIN` to `PDD_CREDIT_MAX`, default 10 to 1000, on a card of their own or one of the packs in `PDD_CREDIT_PACKS`, default 10 and 30) at `PDD_CREDIT_PRICE_CENTS` (default R$1.50 a credit), see the price, and pay with Pix or card on Asaas's checkout page, so the site never sees payment details. Coming back from Asaas proves nothing: the credits are added when Asaas's webhook (`/api/payments/webhook`, checked against `PDD_ASAAS_WEBHOOK_TOKEN`) says the checkout was paid, once even if it's sent again. Needs `PDD_ASAAS_API_KEY` and `PDD_ASAAS_WEBHOOK_TOKEN`; `PDD_ASAAS_SANDBOX` (default true) uses Asaas's test environment. Setting it up: `deploy/LIGHTSAIL.md`, step 9. Locally, Asaas can't reach the webhook, so a sandbox payment stays "waiting for confirmation".
- **ROI and viability** (`roi.py`, `assess.py`, `web/roi_templates.py`): every project gets an AI assessment (a stage after the steps, `assessment.json`, one call): whether the process is a good candidate for automation, what speaks for and against it, recommendations, the time of one run without the narrator's pauses, and how often it runs only when the narration says so (kept only if its words are in the transcript). ROI templates (the ROI page) hold a company's assumptions: cost per hour, cost to build and to run an automation, the period, hours of a full-time person, currency and which indicators it reports (hours saved, savings, net savings, payback, ROI, FTE freed). A project made with one gets a copy (`roi.json`), editable on its page, where the runs per month and time per run default to the assessment's figures (or the recorded time). Saving (`PUT /api/runs/<id>/roi`) writes the document again from the saved results (`pipeline.rebuild_document`: no model calls, no credits). The document gets "Return on Investment (ROI)" and "Automation Viability" sections after "6.3 Metrics and Volumes" in Pidi's template (numbered 6.4 and 6.5; the metrics table also answers how often and how long) or at the end of a company template; their fixed texts are in English, Portuguese or Spanish by the template's language, and the assessment is written by the model in the template's language.
- **Automation spec** (`spec.py`, `GET /api/runs/<id>/automation`, "Download automation spec" on a finished project): the process as Markdown for an AI agent that builds the automation (a separate project), in a zip with its screenshots (`automation.md`, `screenshots/S<n>.jpg`). Written from the run's saved results each time, with no model calls: front matter (`spec_version`, applications, inputs, decisions, runs per month and time per run with where they came from, viability; its values are JSON, so it reads as YAML too), the names to match exactly, every step with its application, start time, decision, the text visible on its screens and its screenshot, business rules, exceptions, the viability assessment and the open questions the recording doesn't answer. It describes the process, never a technology, so one spec can feed a Python or a UiPath generator. `python spec.py <run folder>` writes it for a run made on the command line. Change `SPEC_VERSION` when a change would break its readers.
- **Account**: the name can be changed on the Account page; the email follows the Google account.
- **Privacy (LGPD)**: the privacy notice at `/privacy` names the operator and contact (`PDD_OPERATOR_NAME`, `PDD_CONTACT_EMAIL`). People accept it before their first project (and again when it changes), and confirm they may upload each recording. On the Account page they download everything kept about them (JSON) or delete their account with all its projects, templates and credits. When the notice changes, update `PRIVACY_VERSION` in `web/auth.py` and everyone is asked to accept it again.
- **Retention**: `PDD_RETENTION_DAYS` (default 30) after a project finishes, its recording is deleted: the video, its audio and the frames the document doesn't use. The document, its screenshots and the process model stay. 0 keeps recordings until the project is deleted.
- **Monitoring**: failed projects and server errors are emailed to `PDD_ALERT_EMAIL` (the same kind of problem at most every 15 minutes). `/api/health` answers 503 if a worker thread died, for an uptime monitor.
- **Uploads** are limited to `PDD_MAX_UPLOAD_MB` (default 2048) and must contain a video stream.
- **Templates**: on the Templates page people upload their company's PDD template (.docx), see what the analysis decided for every part of it, and pick it when uploading a recording. Each run copies the template and its analysis into its folder, so deleting a template doesn't change documents made with it.
- **Before putting it on the internet**:
  - Serve it behind HTTPS (e.g. a reverse proxy such as Caddy or nginx) and set `PDD_COOKIE_SECURE=true`.
  - Run a single server process, since the job queue lives in it.
  - Recordings and documents are confidential company material, so restrict sign-up and back up `data/`.

The API is described at http://127.0.0.1:8000/api/docs.

To put it on a server, `compose.yaml` runs the app (`Dockerfile`) behind Caddy, which handles HTTPS; `deploy/LIGHTSAIL.md` is the step-by-step for AWS Lightsail, with `deploy/env.example` as the settings to fill in.

## Checking a run

The run report printed at the end (and saved in `report.json`) shows where frames could get lost:

- `frames_kept` → `frames_referenced` → `images_inserted`: how many screenshots survive each stage.
- `frames_not_used`: frames no step cites; open them in `frames/` to see whether the model skipped something relevant.
- `steps_framed_by_narration`: steps the outline attached no frames to, whose frames were then taken from the moment they are narrated (e.g. "delete the old data" over a table that just looks empty).
- `steps_without_image`: steps with no screenshot, typically actions that are neither shown nor narrated.
- `steps_not_refined`, `invalid_frame_refs`: steps the refinement skipped (their outline version is used) and frame ids the model made up.
- `names_fixed`, `names_verified`, `names_fixed_by_vision`: misread names replaced from the outline's list, and similar names checked against the full-resolution frames (with how many frames showed each).
- `applications`, `decision_points`, `recorded_seconds`: what the overview table shows. The recorded time includes the narrator's explanations, so it overstates the time the task takes.

If relevant screens are missing from `frames/` itself, lower `--change-threshold`; if there are too many near-duplicates, raise it.

## Tests

```bash
OPENAI_API_KEY=unused .venv/bin/python -m pytest tests
```

The tests use a synthetic screen recording and fake model/transcription, so they run offline. `tests/test_web.py` covers the website's API (accounts, uploads, background runs, access between users) with the same fakes. `python -m tests.measure_frames create_video` reports how many of the synthetic recording's known screen states the frame extractor captures.
