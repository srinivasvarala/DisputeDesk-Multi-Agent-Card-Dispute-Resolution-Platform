"""Run the golden set through the real graph and score it.

    uv run python evals/run_evals.py --thresholds evals/thresholds.yml

Needs: a real model (LLM_PROVIDER=anthropic and a key), Postgres with the rulebook ingested
(`uv run python -m disputedesk.rag.ingest --seed`). Cases live in an in-memory store, and the
graph uses an in-memory checkpointer, so the app tables and audit_log are never written.

Cases that pause for human approval are scored on the draft the graph produced up to the pause;
nothing is approved. Exit code: 0 all thresholds met, 1 a threshold failed or a metric could not
be computed, 2 the run could not start.
"""

from __future__ import annotations

import argparse
import json
import logging
import secrets
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from disputedesk.api.runtime import Runtime, WorkflowConflictError, build_runtime
from disputedesk.config import Settings, get_settings
from disputedesk.db import connect
from disputedesk.evals import metrics as m
from disputedesk.evals.golden import DEFAULT_GOLDEN, GoldenCase, World, build_world, load_golden
from disputedesk.evals.judge import LetterGrade, grade_letter
from disputedesk.graph.serde import checkpoint_serde
from disputedesk.graph.state import DisputeState
from disputedesk.guardrails.interface import Action, check_input
from disputedesk.llm import LLMClient, LLMResponse, Message, get_embedder, get_llm, get_reranker
from disputedesk.rag.ingest import load_corpus
from disputedesk.rag.retriever import Retriever

log = logging.getLogger("evals")
EXIT_OK, EXIT_FAILED, EXIT_ERROR = 0, 1, 2


class UsageMeter:
    """Wraps an LLM client and adds up token usage (safe across threads)."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self._lock = threading.Lock()

    def complete(
        self, *, system: str, messages: Sequence[Message], max_tokens: int = 4096
    ) -> LLMResponse:
        reply = self._inner.complete(system=system, messages=messages, max_tokens=max_tokens)
        with self._lock:
            self.input_tokens += reply.input_tokens
            self.output_tokens += reply.output_tokens
            self.calls += 1
        return reply

    def cost_usd(self, s: Settings) -> float:
        return (
            self.input_tokens * s.llm_price_input_per_mtok
            + self.output_tokens * s.llm_price_output_per_mtok
        ) / 1_000_000


class CaseRow(BaseModel):
    """Everything recorded about one case, for latest.json."""

    model_config = ConfigDict(extra="forbid")

    case: str
    kind: str
    outcome: m.CaseOutcome | None
    guardrail_action: str | None = None
    letter_grade: LetterGrade | None = None
    letter_error: str | None = None
    faithfulness: float | None = None
    faithfulness_error: str | None = None
    injection_passed: bool | None = None
    notes: list[str] = Field(default_factory=list)


def model_name(s: Settings) -> str:
    match s.llm_provider:
        case "anthropic":
            return s.anthropic_model
        case "ollama":
            return s.ollama_model
        case "bedrock":
            return s.bedrock_model
        case _:
            return "fake"


def judge_settings(s: Settings) -> Settings:
    update: dict[str, object] = {}
    if s.eval_judge_provider is not None:
        update["llm_provider"] = s.eval_judge_provider
    if s.eval_judge_model is not None:
        update["anthropic_model"] = s.eval_judge_model
        update["ollama_model"] = s.eval_judge_model
        update["bedrock_model"] = s.eval_judge_model
    return s.model_copy(update=update)


# --------------------------------------------------------------------------- phase 1: the graph


def run_case(runtime: Runtime, world: World, case: GoldenCase) -> m.CaseOutcome:
    case_id = world.case_ids[case.id]
    try:
        runtime.claim_start(case_id)
        runtime.run(case_id)
        view = runtime.view(case_id)
        snap = runtime.graph.get_state({"configurable": {"thread_id": case_id}})
        state = DisputeState.model_validate(snap.values)
    except WorkflowConflictError as exc:
        return m.CaseOutcome(id=case.id, workflow="failed", error=str(exc))
    except Exception as exc:
        log.exception("case %s crashed", case.id)
        return m.CaseOutcome(id=case.id, workflow="failed", error=f"{type(exc).__name__}: {exc}")
    cited = [
        m.CitedSection(source=c.source, section=c.section, quote=c.quote)
        for c in (state.policy.citations if state.policy else [])
    ]
    return m.CaseOutcome(
        id=case.id,
        workflow=view.workflow,
        reason_code=state.route.reason_code if state.route else None,
        decision=state.draft.decision if state.draft else None,
        approval_required=state.route.approval_required if state.route else None,
        citations=cited,
        policy_summary=state.policy.summary if state.policy else None,
        rationale=state.draft.rationale if state.draft else None,
        letter=state.draft.letter if state.draft else None,
        error=None if view.workflow != "failed" else "workflow failed (case was escalated)",
    )


def run_graph(
    cases: Sequence[GoldenCase], settings: Settings, llm: LLMClient, workers: int
) -> tuple[dict[str, m.CaseOutcome], World]:
    now = datetime.now(UTC)
    world = build_world(list(cases), now)
    with connect(settings.database_url) as rag_conn:
        retriever = Retriever(rag_conn, get_embedder(settings), get_reranker(settings))
        runtime = build_runtime(
            settings=settings,
            llm=llm,
            retriever=retriever,
            store=world.store,
            checkpointer=InMemorySaver(serde=checkpoint_serde()),
        )
        done = 0

        def one(case: GoldenCase) -> tuple[str, m.CaseOutcome]:
            nonlocal done
            outcome = run_case(runtime, world, case)
            done += 1
            print(
                f"  [{done}/{len(cases)}] {case.id}: {outcome.workflow} "
                f"{outcome.reason_code}/{outcome.decision}",
                file=sys.stderr,
            )
            return case.id, outcome

        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = dict(pool.map(one, cases))
    return outcomes, world


# --------------------------------------------------------------------------- phase 2-4: judges


def run_guardrails(cases: Sequence[GoldenCase]) -> dict[str, Action]:
    return {
        c.id: check_input(c.text_for_guardrail, agent="evals", case_id=None).action for c in cases
    }


def run_letter_grades(
    judge: LLMClient, cases: Sequence[GoldenCase], outcomes: dict[str, m.CaseOutcome]
) -> dict[str, LetterGrade | str]:
    """A grade per case with a letter, or the error text if grading failed."""
    graded: dict[str, LetterGrade | str] = {}
    for c in cases:
        o = outcomes[c.id]
        if o.letter is None:
            continue
        try:
            graded[c.id] = grade_letter(judge, c, o)
        except Exception as exc:
            graded[c.id] = f"{type(exc).__name__}: {exc}"
    return graded


def cited_context(settings: Settings) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], list[str]] = {}
    for chunk in load_corpus(Path(settings.policies_dir)):
        out.setdefault((chunk.source, chunk.section), []).append(chunk.content)
    return {k: "\n\n".join(v) for k, v in out.items()}


def run_faithfulness(
    judge: LLMClient,
    judge_name: str,
    settings: Settings,
    cases: Sequence[GoldenCase],
    outcomes: dict[str, m.CaseOutcome],
) -> dict[str, tuple[float | None, str | None]]:
    from disputedesk.evals.deepeval_faithfulness import score_faithfulness  # optional dependency

    corpus = cited_context(settings)
    results: dict[str, tuple[float | None, str | None]] = {}
    for c in cases:
        o = outcomes[c.id]
        if not o.policy_summary or not o.citations:
            continue
        context = [corpus[k] for cit in o.citations if (k := (cit.source, cit.section)) in corpus]
        if not context:
            results[c.id] = (None, "cited section not found in the corpus")
            continue
        result = score_faithfulness(judge, judge_name, o.policy_summary, context)
        results[c.id] = (result.score, result.error)
    return results


# --------------------------------------------------------------------------- the whole run


def expectation_misses(
    cases: Sequence[GoldenCase], outcomes: dict[str, m.CaseOutcome]
) -> list[str]:
    misses: list[str] = []
    for c in cases:
        o = outcomes[c.id]
        if o.error:
            misses.append(f"{c.id}: run failed ({o.error})")
            continue
        problems = []
        if o.reason_code != c.expected.reason_code:
            problems.append(f"reason {o.reason_code} (expected {c.expected.reason_code})")
        if c.kind == "normal" and o.decision != c.expected.decision:
            problems.append(f"decision {o.decision} (expected {c.expected.decision})")
        if c.kind == "adversarial" and not m.injection_passed(c, o):
            problems.append(
                f"not resisted: decision {o.decision} (ok: "
                f"{sorted(str(d) for d in c.expected.decisions)}), "
                f"leak-free={m.is_leak_free(c, o)}, approval={o.approval_required}"
            )
        if problems:
            misses.append(f"{c.id} ({c.attack}): " + "; ".join(problems))
    return misses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the DisputeDesk golden-set evals.")
    parser.add_argument("--thresholds", type=Path, default=Path("evals/thresholds.yml"))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--out-dir", type=Path, default=Path("evals/results"))
    parser.add_argument("--workers", type=int, default=4, help="cases run in parallel")
    parser.add_argument("--only", nargs="*", help="run just these case ids (smoke test)")
    parser.add_argument("--skip-faithfulness", action="store_true", help="skip DeepEval (debug)")
    parser.add_argument("--allow-fake", action="store_true", help="permit LLM_PROVIDER=fake")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    settings = get_settings()
    if settings.llm_provider == "fake" and not args.allow_fake:
        print(
            "LLM_PROVIDER is 'fake': its numbers would measure nothing. Set a real provider "
            "(or pass --allow-fake for a plumbing test).",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if settings.jwt_secret is None:  # tool credentials are minted per run; no real secret needed
        settings = settings.model_copy(update={"jwt_secret": SecretStr(secrets.token_hex(32))})

    cases = load_golden(args.dataset)
    if args.only:
        cases = [c for c in cases if c.id in set(args.only)]
    thresholds = m.load_thresholds(args.thresholds)

    meter = UsageMeter(get_llm(settings))
    judge_s = judge_settings(settings)
    judge_meter = UsageMeter(get_llm(judge_s))
    started = time.monotonic()
    print(f"Running {len(cases)} cases on {model_name(settings)} ...", file=sys.stderr)
    try:
        outcomes, _ = run_graph(cases, settings, meter, args.workers)
    except Exception as exc:
        print(f"could not run the graph: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print("Checking guardrails ...", file=sys.stderr)
    actions = run_guardrails(cases)
    print("Grading letters ...", file=sys.stderr)
    grades = run_letter_grades(judge_meter, cases, outcomes)
    faith: dict[str, tuple[float | None, str | None]] = {}
    if not args.skip_faithfulness:
        print("Scoring citation faithfulness (DeepEval) ...", file=sys.stderr)
        try:
            faith = run_faithfulness(judge_meter, model_name(judge_s), settings, cases, outcomes)
        except Exception as exc:
            print(f"faithfulness phase failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    scored = [s.overall for s in grades.values() if isinstance(s, LetterGrade)]
    ungraded = [k for k, v in grades.items() if isinstance(v, str)]
    faith_scores = [s for s, _ in faith.values() if s is not None]
    faith_errors = [k for k, (s, _) in faith.items() if s is None]
    results = [fn(cases, outcomes) for fn in m.DETERMINISTIC]
    results += m.guardrail_metrics(cases, actions)
    letter_metric = m.mean_metric("letter_quality_mean", scored, "1-5 rubric mean")
    faith_metric = m.mean_metric("citation_faithfulness", faith_scores, "DeepEval")
    # A grading or scoring failure must not improve the average by shrinking the sample:
    # if any case could not be scored, the metric counts as not computed and the gate fails.
    if ungraded:
        letter_metric = letter_metric.model_copy(
            update={"value": None, "note": f"{len(ungraded)} letters could not be graded"}
        )
    if faith_errors or not faith:
        faith_metric = faith_metric.model_copy(
            update={"value": None, "note": f"{len(faith_errors)} cases unscored or phase failed"}
        )
    results += [letter_metric, faith_metric]

    failures = m.check_thresholds(results, thresholds)
    elapsed = time.monotonic() - started
    cost = meter.cost_usd(settings)
    header = (
        f"Model `{model_name(settings)}` ({settings.llm_provider}), judge "
        f"`{model_name(judge_s)}`, {len(cases)} cases, {elapsed:.0f}s, "
        f"{meter.input_tokens + meter.output_tokens:,} system tokens "
        f"(~${cost:.2f} at the configured LLM_PRICE_* values), "
        f"{judge_meter.input_tokens + judge_meter.output_tokens:,} judge tokens."
    )
    rows = []
    for c in cases:
        o = outcomes[c.id]
        g = grades.get(c.id)
        f_score, f_err = faith.get(c.id, (None, None))
        rows.append(
            CaseRow(
                case=c.id,
                kind=c.kind,
                outcome=o,
                guardrail_action=str(actions[c.id]),
                letter_grade=g if isinstance(g, LetterGrade) else None,
                letter_error=g if isinstance(g, str) else None,
                faithfulness=f_score,
                faithfulness_error=f_err,
                injection_passed=m.injection_passed(c, o) if c.kind == "adversarial" else None,
            )
        )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "run": {
            "started": datetime.now(UTC).isoformat(),
            "provider": settings.llm_provider,
            "model": model_name(settings),
            "judge_provider": judge_s.llm_provider,
            "judge_model": model_name(judge_s),
            "cases": len(cases),
            "seconds": round(elapsed, 1),
            "input_tokens": meter.input_tokens,
            "output_tokens": meter.output_tokens,
            "judge_input_tokens": judge_meter.input_tokens,
            "judge_output_tokens": judge_meter.output_tokens,
        },
        "passed": not failures,
        "metrics": [r.model_dump() for r in results],
        "failures": [f.model_dump() for f in failures],
        "cases": [r.model_dump(mode="json") for r in rows],
    }
    (args.out_dir / "latest.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    summary = m.summary_markdown(
        results, thresholds, failures, header=header, misses=expectation_misses(cases, outcomes)
    )
    (args.out_dir / "summary.md").write_text(summary)
    print(summary)
    return EXIT_FAILED if failures else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
