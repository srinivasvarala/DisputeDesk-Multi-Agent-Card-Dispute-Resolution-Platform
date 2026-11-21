"""Measure the input guardrail on its own, without running the agent graph.

    GUARDRAIL_JUDGE=true LLM_PROVIDER=anthropic uv run python scripts/guardrail_check.py

Reports catch rate and false-positive rate on two sets:
- the held-out dev set (evals/datasets/guardrail_dev.jsonl): what the patterns were tuned on, and
  the better estimate of how the guardrail generalises;
- the golden set (evals/datasets/golden.jsonl): what the eval gate scores. The heuristics were
  written after reading these attacks, so treat this number as less than blind.

With GUARDRAIL_JUDGE unset or false it measures the heuristics alone (no model calls).
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from disputedesk.config import get_settings
from disputedesk.evals import metrics as m
from disputedesk.evals.golden import load_golden
from disputedesk.evals.runner import run_guardrails
from disputedesk.guardrails.interface import Action, check_input

DEV = Path(__file__).parents[1] / "evals" / "datasets" / "guardrail_dev.jsonl"


def run_dev() -> int:
    rows = [json.loads(line) for line in DEV.read_text().splitlines() if line.strip()]
    with ThreadPoolExecutor(max_workers=4) as pool:
        actions = list(
            pool.map(lambda r: check_input(r["text"], agent="guardrail_check").action, rows)
        )
    attacks = [(r, a) for r, a in zip(rows, actions, strict=True) if r["attack"]]
    clean = [(r, a) for r, a in zip(rows, actions, strict=True) if not r["attack"]]
    caught = [r for r, a in attacks if a != Action.ALLOW]
    wrongly = [r for r, a in clean if a == Action.BLOCK]
    print(
        f"DEV     catch {len(caught)}/{len(attacks)}   false positives {len(wrongly)}/{len(clean)}"
    )
    for r, a in attacks:
        if a == Action.ALLOW:
            print(f"   missed [{r['kind']}] {r['text'][:90]}")
    for r in wrongly:
        print(f"   false positive: {r['text'][:90]}")
    return len(attacks) - len(caught) + len(wrongly)


def run_golden() -> int:
    cases = load_golden()
    actions = run_guardrails(cases)
    problems = 0
    for metric in m.guardrail_metrics(cases, actions):
        hits = f"{metric.numerator:.0f}/{metric.denominator}"
        print(f"GOLDEN  {metric.name} = {metric.value:.3f} ({hits})")
    for c in cases:
        expected = c.guardrail.value
        got = actions[c.id].value
        wrong = (expected == "block" and got != "block") or (
            expected == "redact" and got != "redact"
        )
        wrong = wrong or (c.kind == "normal" and got == "block")
        if wrong:
            problems += 1
            print(f"   {c.id} ({c.kind}) expected {expected}, got {got}")
    return problems


def main() -> int:
    judge = get_settings().guardrail_judge
    print(
        f"provider={get_settings().llm_provider} judge={'on' if judge else 'off (heuristics only)'}"
    )
    problems = run_dev() + run_golden()
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
