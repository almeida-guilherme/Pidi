"""
Phase 5: measuring how good the steps are, and catching changes that make them worse.

An evaluation case is a finished project whose steps a person checked (ideally approved on its review page):
its saved screen observations, narration and frame list, and the steps it should have. A run writes the steps
again from those saved inputs, several times per case (the stage whose results vary: 10 to 14 steps from the same
recording), and scores each result against the expected steps. Nothing re-reads the video, so a run costs one
step-writing pass per repeat (about $0.05 on OpenAI's defaults, nothing on Ollama).

Cases live in data/eval/cases/ (ignored by git: recordings and their steps are often confidential); results in
data/eval/results/.

    .venv/bin/python -m evaluation.make_case <project id> [--name ...]   # a case from a finished project
    .venv/bin/python -m evaluation.run [--repeats 3] [--provider openai --model ...]
"""
