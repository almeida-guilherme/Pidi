"""
How close a run's steps are to a case's expected steps.

A produced step matches an expected step when they cover the same part of the recording (the frames they cite
overlap) and say much the same thing (their wording is similar); each expected step matches at most one produced
step, best pairs first. From the matches:

- found: the share of expected steps that were produced (a missed step lowers it);
- extra: the share of produced steps that match nothing (a split or an invented step raises it);
- f1: the two together, the headline score;
- applications: the share of expected application names that were produced (spelled the same, case aside).
"""
from difflib import SequenceMatcher

# The least similarity for two steps to count as the same step. Frames weigh more than wording: two runs word the
# same step differently far more often than they cite different frames for it.
MATCH_AT = 0.35
FRAME_WEIGHT = 0.6


def _frames(a: list[str], b: list[str]) -> float | None:
    a, b = set(a or []), set(b or [])
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


def _words(a: str, b: str) -> float:
    return SequenceMatcher(None, " ".join(a.lower().split()), " ".join(b.lower().split())).ratio()


def similarity(expected: dict, produced: dict) -> float:
    words = _words(expected["instruction"], produced["instruction"])
    frames = _frames(expected.get("frame_ids"), produced.get("frame_ids"))
    return words if frames is None else FRAME_WEIGHT * frames + (1 - FRAME_WEIGHT) * words


def match(expected: list[dict], produced: list[dict]) -> list[tuple[int, int, float]]:
    """(expected index, produced index, similarity), best pairs first, each step used once."""
    pairs = sorted(((similarity(e, p), i, j) for i, e in enumerate(expected) for j, p in enumerate(produced)),
                   reverse=True)
    used_e, used_p, out = set(), set(), []
    for score, i, j in pairs:
        if score < MATCH_AT:
            break
        if i not in used_e and j not in used_p:
            used_e.add(i)
            used_p.add(j)
            out.append((i, j, score))
    return out


def score(case: dict, produced: list[dict]) -> dict:
    expected = case["expected"]["steps"]
    pairs = match(expected, produced)
    found = len(pairs) / len(expected) if expected else 1.0
    extra = (len(produced) - len(pairs)) / len(produced) if produced else 0.0
    precision = 1 - extra
    f1 = 2 * found * precision / (found + precision) if found + precision else 0.0
    want = {a.lower() for a in case["expected"].get("applications", [])}
    got = {p.get("application", "").lower() for p in produced if p.get("application")}
    return {
        "steps": len(produced), "expected_steps": len(expected),
        "found": round(found, 3), "extra": round(extra, 3), "f1": round(f1, 3),
        "applications": round(len(want & got) / len(want), 3) if want else 1.0,
        "missed": [expected[i]["number"] for i in range(len(expected)) if i not in {a for a, _, _ in pairs}],
        "unmatched": [produced[j]["number"] for j in range(len(produced)) if j not in {b for _, b, _ in pairs}],
    }
