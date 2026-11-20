"""
Records examples/sample_app (an invented finance portal) doing an invoice approval, as a screen recording for
Pidi's example: headless Chrome takes a screenshot after every action and ffmpeg joins them into a video.

    .venv/bin/python -m examples.make_sample [examples/invoice-approval.mp4]

Needs Google Chrome (or Chromium) and ffmpeg. The process, as an accounts-payable clerk would do it:
sign in, filter the invoices pending approval, and for each one compare it with its purchase order: approve it
when the amounts match, flag it for review when they don't; then export the approved invoices.
"""
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

APP = Path(__file__).parent / "sample_app" / "index.html"
WIDTH, HEIGHT = 1366, 768
PORT = 9341


class Recorder:
    def __init__(self, ws, frames: Path):
        self.ws, self.frames, self.n, self.shots = ws, frames, 0, []
        self.cursor = (WIDTH // 2, HEIGHT // 2)

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == self.n:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def js(self, code):
        return (await self.call("Runtime.evaluate", expression=code, returnByValue=True))["result"].get("value")

    async def shot(self, seconds: float):
        """The screen as it is now, shown for this long in the video."""
        await asyncio.sleep(0.12)
        data = (await self.call("Page.captureScreenshot", format="png"))["data"]
        path = self.frames / f"{len(self.shots):04d}.png"
        path.write_bytes(base64.b64decode(data))
        self.shots.append((path, seconds))

    async def move(self, x, y):
        """The cursor glides there in a few frames, as a hand would move it."""
        x0, y0 = self.cursor
        for k in (0.35, 0.75, 1.0):
            await self.js(f"document.getElementById('cursor').style.transform='translate({x0 + (x - x0) * k}px,{y0 + (y - y0) * k}px)'")
            await self.shot(0.12)
        self.cursor = (x, y)

    async def center(self, selector):
        box = await self.js(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
                            " return [r.x + r.width / 2, r.y + r.height / 2]; })()")
        return int(box[0]), int(box[1])

    async def click(self, selector, hold=1.6):
        x, y = await self.center(selector)
        await self.move(x, y)
        for kind in ("mousePressed", "mouseReleased"):
            await self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)
        await self.shot(hold)

    async def type(self, selector, text, hold=1.0):
        await self.click(selector, hold=0.4)
        for i in range(0, len(text), 2):  # two characters per frame: a quick typist
            await self.call("Input.insertText", text=text[i:i + 2])
            await self.shot(0.14)
        await self.shot(hold)

    async def choose(self, selector, value, hold=2.0):
        """A dropdown: clicked, then the option picked (headless Chrome draws no open list)."""
        await self.click(selector, hold=0.6)
        await self.js(f"(() => {{ const s = document.querySelector({json.dumps(selector)}); s.value = {json.dumps(value)};"
                      " s.dispatchEvent(new Event('change')); })()")
        await self.shot(hold)

    async def review_invoice(self, row, comment, approve):
        await self.click(f"#row-{row}", hold=2.5)       # the invoice
        await self.click("#view-po", hold=3.5)          # its purchase order, next to it: do the amounts match?
        await self.click("#approve" if approve else "#flag", hold=1.2)
        await self.type("#comment", comment)
        await self.click("#confirm", hold=2.5)

    async def process(self):
        await self.shot(2.0)
        await self.type("#user", "maria.santos")
        await self.type("#password", "••••••••••", hold=0.6)
        await self.click("#signin", hold=3.0)
        await self.click("#nav-invoices", hold=2.5)
        await self.choose("#status", "Pending approval", hold=2.5)
        await self.review_invoice("INV-20431", "Matches PO-7781.", approve=True)
        await self.click("#back", hold=2.0)
        await self.review_invoice("INV-20437", "Amount is $110.50 over PO-7795. Asked the vendor for a corrected invoice.",
                                  approve=False)
        await self.click("#back", hold=2.0)
        await self.review_invoice("INV-20440", "Matches PO-7802.", approve=True)
        await self.click("#back", hold=1.5)
        await self.choose("#status", "Approved", hold=2.5)
        await self.click("#export", hold=4.0)


async def record(frames: Path) -> list[tuple[Path, float]]:
    for _ in range(100):
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list"))
            page = next(t for t in targets if t["type"] == "page")
            break
        except Exception:
            time.sleep(0.1)
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
        rec = Recorder(ws, frames)
        await rec.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
        await rec.call("Page.enable")
        await rec.call("Page.navigate", url=APP.resolve().as_uri())
        await asyncio.sleep(1.0)
        await rec.process()
        return rec.shots


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "invoice-approval.mp4")
    browser = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not browser or not shutil.which("ffmpeg"):
        sys.exit("Needs Google Chrome (or Chromium) and ffmpeg.")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        chrome = subprocess.Popen([browser, "--headless=new", f"--remote-debugging-port={PORT}", "--no-first-run",
                                   "--hide-scrollbars", f"--user-data-dir={tmp / 'chrome'}", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            (tmp / "frames").mkdir()
            shots = asyncio.run(record(tmp / "frames"))
        finally:
            chrome.terminate()
        listing = tmp / "frames.txt"
        listing.write_text("".join(f"file '{p}'\nduration {d}\n" for p, d in shots) + f"file '{shots[-1][0]}'\n")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-vf", "fps=10,format=yuv420p", "-c:v", "libx264", "-crf", "23", str(out)], check=True)
    print(f"{out}: {sum(d for _, d in shots):.0f} s, {len(shots)} screenshots")


if __name__ == "__main__":
    main()
