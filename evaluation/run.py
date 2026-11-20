"""
Writes each case's steps again from its saved inputs, `--repeats` times, and scores them (evaluation/score.py).
Prints a table per case and overall, and saves everything to data/eval/results/<date>.json.

    .venv/bin/python -m evaluation.run                         # OpenAI's default writing model, OPENAI_API_KEY
    .venv/bin/python -m evaluation.run --provider ollama --model qwen3.5:9b --repeats 2
    .venv/bin/python -m evaluation.run --case <case folder name> --repeats 5

The key comes from the environment: OPENAI_API_KEY, ANTHROPIC_API_KEY or GOOGLE_API_KEY (or PDD_OLLAMA_URL).
"""
import argparse
import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

from evaluation.make_case import CASES
from evaluation.score import score

RESULTS = CASES.parent / "results"
KEY_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GOOGLE_API_KEY"}


def write_steps(folder: Path, llm) -> list[dict]:
    """The case's steps written again from its saved inputs, as the pipeline's step-writing stage does."""
    from structure import structure_pdd
    load = lambda name: json.loads((folder / name).read_text(encoding="utf-8")) if (folder / name).exists() else None  # noqa: E731
    case = load("case.json")
    analysis = load("template_analysis.json")
    model, _ = structure_pdd(load("captions.json") or [], load("transcript.json"), load("frames.json") or [], llm,
                             language=analysis.get("language") if analysis else None,
                             step_example=case.get("step_example"))
    return [{"number": s.number, "instruction": s.instruction, "application": s.application, "frame_ids": s.frame_ids}
            for s in model.as_is]


def run(cases: list[Path], llm, repeats: int, progress=print) -> dict:
    from costs import cost_by_model, measured
    out = {"cases": [], "started": datetime.now().isoformat(timespec="seconds")}
    for folder in cases:
        case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        runs = []
        for n in range(repeats):
            start = time.time()
            with measured() as usage:
                try:
                    produced = write_steps(folder, llm)
                    result = score(case, produced)
                except Exception as exc:  # one failed repeat is a result too
                    result = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            costs, unpriced = cost_by_model(usage.usage_metadata)
            result.update(seconds=round(time.time() - start, 1), cost_usd=round(sum(costs.values()), 4),
                          unpriced=unpriced)
            runs.append(result)
            progress(f"  {case['name']}, run {n + 1}/{repeats}: "
                     + (result["error"] if "error" in result else
                        f"{result['steps']} steps (expected {result['expected_steps']}), f1 {result['f1']}, "
                        f"found {result['found']}, extra {result['extra']}, ${result['cost_usd']}, {result['seconds']} s"))
        out["cases"].append({"case": folder.name, "name": case["name"], "approved": case.get("approved"),
                             "runs": runs, "summary": summarize(runs)})
    out["summary"] = summarize([r for c in out["cases"] for r in c["runs"]])
    return out


def summarize(runs: list[dict]) -> dict:
    ok = [r for r in runs if "error" not in r]
    if not ok:
        return {"runs": len(runs), "failed": len(runs)}
    mean = lambda key: round(statistics.mean(r[key] for r in ok), 3)  # noqa: E731
    counts = [r["steps"] for r in ok]
    return {"runs": len(runs), "failed": len(runs) - len(ok), "f1": mean("f1"), "found": mean("found"),
            "extra": mean("extra"), "applications": mean("applications"),
            "steps_min": min(counts), "steps_max": max(counts), "cost_usd": round(sum(r["cost_usd"] for r in ok), 4)}


def main() -> None:
    import providers
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default="openai", choices=list(providers.PROVIDERS))
    parser.add_argument("--model", help="the writing model (default: the provider's)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--case", action="append", help="only this case (its folder name); repeatable")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(".env")
    cases = sorted(p for p in CASES.glob("*") if (p / "case.json").exists() and (not args.case or p.name in args.case))
    if not cases:
        raise SystemExit(f"No cases in {CASES}. Make one with: python -m evaluation.make_case <project id>")
    ai = {"provider": args.provider, "model": args.model,
          "api_key": os.getenv(KEY_VARS[args.provider]) if args.provider in KEY_VARS else None}
    llm = providers.writing_model(ai)
    print(f"{len(cases)} case(s), {args.repeats} run(s) each, on {args.provider} {providers.models_for(ai)[1]}")
    result = run(cases, llm, args.repeats)
    result.update(provider=args.provider, model=providers.models_for(ai)[1])
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{datetime.now():%Y-%m-%d-%H%M}-{args.provider}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    s = result["summary"]
    print(f"\nOverall: f1 {s.get('f1')}, found {s.get('found')}, extra {s.get('extra')}, applications "
          f"{s.get('applications')}, {s.get('steps_min')}-{s.get('steps_max')} steps, ${s.get('cost_usd')} "
          f"({s.get('failed', 0)} failed of {s['runs']})\nSaved to {path}")


if __name__ == "__main__":
    main()
