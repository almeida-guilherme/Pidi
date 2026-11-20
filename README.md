# Pidi

**Record yourself doing a task on your computer. Pidi writes the Process Design Document.**

A Process Design Document (PDD) is what RPA and automation teams write before they automate a process: what the process is for, a flowchart, every step with a screenshot, the applications, inputs, business rules and exceptions. Writing one by hand takes hours of pausing a recording and copying screenshots. Pidi watches the recording (and listens to the narration, if there is one) and writes it in Word, in your company's own template or in Pidi's.

What it does:

- **Steps with screenshots**: it keeps a frame whenever the screen changes, reads each one with a vision model, and writes numbered steps with the application, the recorded time and the screenshot.
- **Your template**: upload your company's PDD template (.docx). Pidi works out which parts are fixed text, which are examples to remove and which to write, and keeps its cover, styles and numbering.
- **Review and redo**: read the steps, say what's wrong ("step 4 is two steps", "the file name is misspelled") and the AI fixes those steps again, checking the recording. Every version is kept and can be undone.
- **Never invents**: what the recording doesn't say (contacts, volumes, versions) is left as a highlighted "to fill" marker.
- **ROI and viability**: an assessment of whether the process is worth automating, and an ROI section from your own cost assumptions.
- **Automation spec**: the process as Markdown plus screenshots, for an AI agent or a developer that builds the automation.
- **Any language**: English and Brazilian Portuguese are built in; any other language is translated on first use.
- **Bring your own AI**: OpenAI, Anthropic (Claude), Google (Gemini) or a local Ollama. A dashboard shows what each project cost.

> **Status: early version.** It has been used for real on OpenAI's models. Claude, Gemini, Ollama and the server-side Whisper are supported and tested offline, but have had little real use. Issues and pull requests are welcome.

## Quick start: the command line

The fastest way to try it: one recording in, one .docx out. You need Python 3.12, `ffmpeg`, `graphviz` and an OpenAI API key.

```bash
sudo apt install ffmpeg graphviz          # macOS: brew install ffmpeg graphviz
git clone https://github.com/almeida-guilherme/Pidi.git && cd Pidi
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
echo "OPENAI_API_KEY=sk-..." > .env

.venv/bin/python main.py path/to/recording.mp4               # English, Pidi's template
.venv/bin/python main.py recording.mp4 --language pt-BR      # another language
.venv/bin/python main.py recording.mp4 --template PDD_Template.docx   # your company's template
```

The document is written to `runs/<recording>-<date>/output/PDD_final.docx`, next to everything the run produced (frames, captions, transcript, the process model and a report with its cost). On OpenAI's default models, a recording costs about **$0.10 per minute**.

No recording at hand? [`examples/`](examples/) has a short one of an invented finance portal, and [the document Pidi wrote from it](examples/invoice-approval-PDD.pdf).

Tips for a good recording: narrate what you're doing and why, go at a normal pace, and let each screen settle for a moment before moving on.

## Quick start: the website

The website adds accounts, a project list, the review-and-redo page, company templates, ROI templates and the cost dashboard.

1. Turn on the local login, for using Pidi on your own machine. Add this to `.env`:

   ```bash
   PDD_LOCAL_LOGIN=true
   ```

2. Run it with Docker:

   ```bash
   docker build -t pidi .
   docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -v pidi-data:/data pidi
   ```

   Or without Docker (Node 22 builds the pages once):

   ```bash
   cd web/frontend && npm ci && npm run build && cd ../..
   .venv/bin/uvicorn web.app:create_app --factory --host 127.0.0.1 --port 8000
   ```

3. When it starts, the server prints a log-in link (`http://127.0.0.1:8000/api/auth/local?token=...`). Anyone with that link can log in, so keep the local login for your own machine.

For a site other people use, they log in with Google instead: create an OAuth client in [Google Cloud Console](https://console.cloud.google.com/) (APIs & Services → Credentials → *Create credentials → OAuth client ID*, Web application, redirect URI `<site>/api/auth/google/callback`), put its id and secret in `PDD_GOOGLE_CLIENT_ID` and `PDD_GOOGLE_CLIENT_SECRET`, and turn the local login off.

Once logged in, connect your AI provider on the **Account** page. Keys are checked with the provider, stored encrypted, and never shown again.

To run Ollama models, set `PDD_OLLAMA_URL=http://localhost:11434` (a vision model such as `qwen2.5vl`). Without an OpenAI key, the narration is transcribed on your machine with Whisper (`faster-whisper`).

To put it on a server with HTTPS, `compose.yaml` runs the app behind Caddy; [`deploy/LIGHTSAIL.md`](deploy/LIGHTSAIL.md) is a step-by-step for one small server, and [`deploy/env.example`](deploy/env.example) lists every setting. Recordings are often confidential company material: keep sign-up closed (`PDD_ALLOW_SIGNUP=false`) or limited to your domain (`PDD_SIGNUP_DOMAINS`), and back up `data/`.

## How it works

```
recording ─ frames ─ captions (vision model) ─ narration (Whisper) ─ steps ─ name check ─ template fill ─ .docx
```

1. **Frames**: samples the video and keeps a frame whenever the screen changes and settles.
2. **Captions**: a vision model describes each frame: the screen, the action, the visible text.
3. **Narration**: Whisper transcribes the audio, with timestamps.
4. **Steps**: one call sees the whole recording and decides the ordered steps, inputs, rules and exceptions; then the steps are refined window by window with the detailed captions.
5. **Name check**: names that captions read differently from frame to frame (e.g. "5.5.26" vs "5.3.26") are re-read at full resolution.
6. **Document**: the template's parts are written from the process model and rendered with python-docx, with a Graphviz flowchart.

The pipeline is a LangGraph graph (`pipeline.py`); the website is FastAPI (`web/`) with a React front end (`web/frontend/`). Every option, setting and stage is described in [`docs/reference.md`](docs/reference.md).

## Development

```bash
OPENAI_API_KEY=unused .venv/bin/python -m pytest tests     # offline: a synthetic recording and fake models
```

To measure the quality of the steps, `evaluation/` re-writes the steps of saved projects several times and scores them against the expected ones (`python -m evaluation.run`; see `evaluation/__init__.py`).

## License

[MIT](LICENSE)
