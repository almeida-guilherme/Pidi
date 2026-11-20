"""
An evaluation case from a finished project: its saved inputs (screen observations, narration, frame list, the
template's analysis and example step) and its current steps as the expected ones. Approve the project on its
review page first (or correct it with redos until it's right): the expected steps are only as good as that.

    .venv/bin/python -m evaluation.make_case <project id> [--name "Invoice approval"]
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

CASES = Path(os.getenv("PDD_EVAL_DIR", "data/eval")) / "cases"
INPUTS = ("captions.json", "transcript.json", "frames.json", "template_analysis.json")


def make_case(run_dir: Path, name: str | None = None, cases: Path = CASES) -> Path:
    model = json.loads((run_dir / "process_model.json").read_text(encoding="utf-8"))
    review = {}
    if (run_dir / "review.json").exists():
        review = json.loads((run_dir / "review.json").read_text(encoding="utf-8"))
    folder = cases / run_dir.name
    folder.mkdir(parents=True, exist_ok=True)
    for file in INPUTS:
        if (run_dir / file).exists():
            shutil.copyfile(run_dir / file, folder / file)
    step_example = None
    if (run_dir / "template_analysis.json").exists() and (run_dir / "template.docx").exists():
        from pipeline import _example_step
        from template import TemplateAnalysis
        analysis = TemplateAnalysis.model_validate_json((run_dir / "template_analysis.json").read_text(encoding="utf-8"))
        step_example = _example_step(str(run_dir / "template.docx"), analysis)
    case = {
        "name": name or model.get("process_name") or run_dir.name,
        "from_project": run_dir.name,
        "approved": review.get("status") == "approved",
        "step_example": step_example,
        "expected": {
            "steps": [{"number": s["number"], "instruction": s["instruction"], "application": s.get("application", ""),
                       "frame_ids": s.get("frame_ids", [])} for s in model["as_is"]],
            "applications": list(dict.fromkeys(s["application"] for s in model["as_is"] if s.get("application"))),
        },
    }
    (folder / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", help="the project's id (its folder in data/runs/), or a path to a run folder")
    parser.add_argument("--name", help="a name for the case")
    args = parser.parse_args()
    run_dir = Path(args.project) if os.path.isdir(args.project) else Path("data/runs") / args.project
    if not (run_dir / "process_model.json").exists():
        sys.exit(f"{run_dir} has no finished steps (process_model.json).")
    folder = make_case(run_dir, args.name)
    case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    print(f"Case \"{case['name']}\" written to {folder} with {len(case['expected']['steps'])} expected steps.")
    if not case["approved"]:
        print("Note: the project isn't approved. Check its steps on its review page: they are the expected ones.")


if __name__ == "__main__":
    main()
