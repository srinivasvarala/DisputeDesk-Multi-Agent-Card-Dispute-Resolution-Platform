"""Retrieval evaluation: hit@k and MRR over a labelled question set.

A question is a hit when any of its expected (source, section) targets appears in the top k.
An expected section also matches its sub-sections: "Return requirement" matches
"Return requirement > Counterfeit items are not returned".

    uv run python -m disputedesk.rag.evaluation          # compare all retrieval configurations
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel, Field

from disputedesk.config import get_settings
from disputedesk.db import connect
from disputedesk.llm import get_embedder, get_reranker
from disputedesk.rag.ingest import SECTION_SEPARATOR, ingest, load_corpus
from disputedesk.rag.retriever import RetrievedChunk, Retriever, SearchMode

DEFAULT_DATASET = Path("evals/datasets/retrieval.jsonl")


class Target(BaseModel):
    source: str
    section: str

    def matches(self, chunk: RetrievedChunk) -> bool:
        return chunk.source == self.source and (
            chunk.section == self.section
            or chunk.section.startswith(self.section + SECTION_SEPARATOR)
        )


class RetrievalCase(BaseModel):
    id: str
    question: str
    expected: list[Target] = Field(min_length=1)


class CaseResult(BaseModel):
    id: str
    question: str
    hit: bool
    rank: int | None = Field(description="1-based rank of the first matching chunk")
    retrieved: list[str] = Field(description="Citations of the top-k chunks, in order")


class RetrievalReport(BaseModel):
    config: str
    k: int
    hit_at_k: float
    mrr: float
    results: list[CaseResult]


class Config(BaseModel):
    name: str
    mode: SearchMode
    rerank: bool


CONFIGS = [
    Config(name="vector", mode="vector", rerank=False),
    Config(name="keyword", mode="keyword", rerank=False),
    Config(name="hybrid (RRF)", mode="hybrid", rerank=False),
    Config(name="hybrid + rerank", mode="hybrid", rerank=True),
]


def load_cases(path: Path = DEFAULT_DATASET) -> list[RetrievalCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    cases = [RetrievalCase.model_validate(json.loads(line)) for line in lines if line.strip()]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate case ids in {path}")
    return cases


def evaluate(
    retriever: Retriever, cases: list[RetrievalCase], config: Config, *, k: int = 5
) -> RetrievalReport:
    results = []
    for case in cases:
        chunks = retriever.search(case.question, k=k, mode=config.mode, rerank=config.rerank)
        rank = next(
            (i for i, c in enumerate(chunks, 1) if any(t.matches(c) for t in case.expected)),
            None,
        )
        results.append(
            CaseResult(
                id=case.id,
                question=case.question,
                hit=rank is not None,
                rank=rank,
                retrieved=[c.citation for c in chunks],
            )
        )
    n = len(results) or 1
    return RetrievalReport(
        config=config.name,
        k=k,
        hit_at_k=sum(r.hit for r in results) / n,
        mrr=sum(1 / r.rank for r in results if r.rank) / n,
        results=results,
    )


def format_reports(reports: list[RetrievalReport]) -> str:
    """Markdown: a summary table, then a per-question rank table (— means a miss)."""
    k = reports[0].k
    lines = [
        f"| Configuration | hit@{k} | MRR |",
        "|---|---|---|",
        *(f"| {r.config} | {r.hit_at_k:.2f} | {r.mrr:.2f} |" for r in reports),
        "",
        "| Question | " + " | ".join(r.config for r in reports) + " |",
        "|---|" + "---|" * len(reports),
    ]
    for i, case in enumerate(reports[0].results):
        ranks = [str(r.results[i].rank) if r.results[i].rank else "—" for r in reports]
        lines.append(f"| {case.id} {case.question} | " + " | ".join(ranks) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare retrieval configurations.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--misses", action="store_true", help="print top-k for each miss")
    args = parser.parse_args(argv)

    settings = get_settings()
    embedder = get_embedder(settings)
    cases = load_cases(args.dataset)
    with connect() as conn:
        ingest(conn, embedder, load_corpus(Path(settings.policies_dir)))  # no-op if current
        retriever = Retriever(conn, embedder, get_reranker(settings))
        reports = [evaluate(retriever, cases, c, k=args.k) for c in CONFIGS]
    print(format_reports(reports))
    if args.misses:
        for report in reports:
            for r in (r for r in report.results if not r.hit):
                print(f"\n[{report.config}] miss {r.id}: {r.question}")
                print("\n".join(f"  {i}. {c}" for i, c in enumerate(r.retrieved, 1)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
