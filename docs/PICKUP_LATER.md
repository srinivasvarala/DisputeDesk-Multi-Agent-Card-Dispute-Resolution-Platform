# Pick up later

Leftover work from the README and demo polish session (2026-11-22). None of it blocks showing the project. The longer-term plan is in [ROADMAP.md](ROADMAP.md).

## State when I stopped

- README restructured around the enterprise features, with the eval deep-dive in [EVALS.md](EVALS.md). Pushed through `0a1ca0e`.
- A real-model demo (`docs/img/disputedesk-real-model-gate.gif`) shows `case_0034` reaching the human gate and resolving.
- The eval gate is still **red on 1 of 9**: decision accuracy 0.781 vs 0.80. Rulebook fixes (commit `fb41059`) were tested on a partial run only.

## To do, in suggested order

1. **Fix the letter addressee bug (about 30 min).** The real-model letter for `case_0034` is labelled "Letter to the cardholder" but opens "Dear Bluefin Gadgets" (the merchant) and asks "you" for invoices. Likely the Resolution prompt or the evidence it is given. Steps: find why the merchant name is used as the recipient, fix it, add a unit test, and consider an eval check that the letter addresses the cardholder. Then re-record or crop the demo GIF, because the letter shows in it.
2. **Re-run the full eval (about 28 min, about $6).** `uv run python evals/run_evals.py --thresholds evals/thresholds.yml`. Run it after the letter fix so one run covers both. Then update the README results table and `docs/EVALS.md`, remove the "fixed but not re-measured" paragraph, and archive a new snapshot in `docs/evals/`.
3. **Measure the output guardrail with the real model.** Its false-positive rate is unknown. The same eval run reports it as extra escalations. Re-run the Promptfoo red team too (not re-run since the guardrail work).
4. **Smoother demo GIF.** The current one has only 16 frames. Re-record with more frames if it looks choppy on GitHub.
5. **Trace screenshots for the real-model run.** Run `docker compose up -d` before the demo so Jaeger and Langfuse capture it.

## Nice to have (skip unless there is time)

- Give the CI eval job its `ANTHROPIC_API_KEY` secret so [`evals.yml`](../.github/workflows/evals.yml) runs on GitHub.
- Separate the judge model from the system model, and repeat each eval run to get a variance estimate.
- Recovery for interrupted runs (a database lock instead of the in-memory guard).
- AWS deployment: ECS Fargate, RDS, Cognito, Bedrock behind `llm.py`.

## Interview prep (more valuable than any of the above)

- A 2-minute spoken walkthrough: problem, the agents-judge and code-enforces split, one eval finding, one honest failure.
- The defence of "is this really agentic?": the agents own the judgement calls, and code owns the invariants (money, permissions, state changes). Concede that evidence tools are code-chosen, and say it was a deliberate least-privilege trade the eval gates could loosen.
- Resume bullet numbers: hit@5 0.92, 23 of 24 held-out attacks caught with 0 false positives, $0.028 and 134 spans for one real case.
