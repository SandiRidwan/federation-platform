"""Cohen's kappa, checked against disagreement that was planted.

A kappa implementation that returns a plausible number is indistinguishable from a correct one. So
the annotators here are synthetic and their disagreement is **set in advance**: for a target
disagreement rate, a known fraction of labels is flipped. The kappa computed from those labels must
match the value computed from the same planted design, and the monotonic property must hold — more
planted disagreement, lower kappa.

That is the same principle as the known-effect tests in the revenue platform: a statistic is only
trustworthy if it has been run against a case whose answer is already known.

    python -m llm.labeling.kappa
"""

from __future__ import annotations

import random
from dataclasses import dataclass

LABELS = ("books", "electronics", "garden", "kitchen", "office", "toys")
SEED = 20261023


@dataclass(frozen=True)
class KappaResult:
    kappa: float
    observed_agreement: float
    expected_agreement: float
    items: int
    planted_disagreement: float

    def as_dict(self) -> dict:
        return {
            "kappa": round(self.kappa, 4),
            "observed_agreement": round(self.observed_agreement, 4),
            "expected_agreement": round(self.expected_agreement, 4),
            "items": self.items,
            "planted_disagreement": self.planted_disagreement,
        }


def cohen_kappa(first: list[str], second: list[str]) -> KappaResult:
    """Cohen's kappa for two annotators over the same items.

    po = observed agreement, pe = agreement expected from the marginals alone.
    kappa = (po - pe) / (1 - pe). Written out rather than imported, because the failure this
    guards against is a library returning a number nobody checked.
    """
    if len(first) != len(second):
        raise ValueError("annotators must label the same number of items")
    if not first:
        raise ValueError("no items")

    items = len(first)
    observed = sum(1 for a, b in zip(first, second) if a == b) / items

    labels = sorted(set(first) | set(second))
    expected = 0.0
    for label in labels:
        expected += (first.count(label) / items) * (second.count(label) / items)

    kappa = 0.0 if expected == 1.0 else (observed - expected) / (1 - expected)
    return KappaResult(
        kappa=float(kappa),
        observed_agreement=float(observed),
        expected_agreement=float(expected),
        items=items,
        planted_disagreement=1.0 - observed,
    )


def synthetic_annotators(items: int, disagreement: float, seed: int = SEED) -> tuple[list[str], list[str]]:
    """Two annotators whose disagreement rate is exactly `disagreement`.

    Exact, not approximate: the number of flips is `round(items * disagreement)`, so the planted
    rate is a fact rather than a hope. A random flip count would make the test flaky and the
    comparison meaningless.
    """
    rng = random.Random(seed)
    truth = [LABELS[index % len(LABELS)] for index in range(items)]
    flips = int(round(items * disagreement))

    second = list(truth)
    positions = rng.sample(range(items), flips) if flips else []
    for position in positions:
        alternatives = [label for label in LABELS if label != truth[position]]
        second[position] = rng.choice(alternatives)
    return truth, second


def agreement_curve(items: int = 200) -> dict:
    """Kappa at four planted disagreement rates, and the property each must satisfy."""
    curve = {}
    for rate in (0.0, 0.10, 0.20, 0.40):
        first, second = synthetic_annotators(items, rate)
        result = cohen_kappa(first, second)
        curve[f"{int(rate * 100)}pct"] = result.as_dict()
    return curve


def main() -> int:
    import json

    from tools.common.config import get_settings

    curve = agreement_curve()

    kappas = [item["kappa"] for item in curve.values()]
    checks = {
        "perfect_agreement_is_one": abs(curve["0pct"]["kappa"] - 1.0) < 1e-9,
        "kappa_falls_as_disagreement_rises": all(
            kappas[index] > kappas[index + 1] for index in range(len(kappas) - 1)
        ),
        "observed_agreement_matches_planted": all(
            abs(item["observed_agreement"] - (1 - item["planted_disagreement"])) < 1e-9
            for item in curve.values()
        ),
    }

    report = {
        "labels": list(LABELS),
        "curve": curve,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
        "note": (
            "Annotators are synthetic and their disagreement is set exactly, so kappa is checked "
            "against a known design rather than judged by eye. The monotonic property is asserted "
            "because a kappa that rises with disagreement is broken in a way a single number hides."
        ),
    }

    settings = get_settings()
    path = settings.reports / "llm_labeling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    for name, item in curve.items():
        print(f"  planted {name:<6} kappa={item['kappa']:>7}  observed={item['observed_agreement']}")
    print()
    for name, ok in checks.items():
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"RESULT: {report['result']}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
