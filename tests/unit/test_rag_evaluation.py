from pathlib import Path

from disputedesk.rag.evaluation import (
    CaseResult,
    RetrievalReport,
    Target,
    format_reports,
    load_cases,
)
from disputedesk.rag.ingest import load_corpus
from disputedesk.rag.retriever import RetrievedChunk

ROOT = Path(__file__).resolve().parents[2]


def _chunk(source: str, section: str) -> RetrievedChunk:
    return RetrievedChunk(
        source=source,
        section=section,
        doc_title="T",
        chunk_index=0,
        content="c",
        score=0.0,
        rrf_score=0.0,
    )


def test_dataset_has_25_unique_questions() -> None:
    cases = load_cases(ROOT / "evals" / "datasets" / "retrieval.jsonl")
    assert len(cases) == 25
    assert len({c.question for c in cases}) == 25


def test_every_expected_target_exists_in_the_corpus() -> None:
    """Guards the labels: a renamed heading must not silently turn a question into a miss."""
    chunks = load_corpus(ROOT / "data" / "policies")
    for case in load_cases(ROOT / "evals" / "datasets" / "retrieval.jsonl"):
        for target in case.expected:
            assert any(target.matches(_chunk(c.source, c.section)) for c in chunks), (
                f"{case.id}: {target} not found in corpus"
            )


def test_target_matches_section_and_subsections_only() -> None:
    target = Target(source="07.md", section="Return requirement")
    assert target.matches(_chunk("07.md", "Return requirement"))
    assert target.matches(
        _chunk("07.md", "Return requirement > Counterfeit items are not returned")
    )
    assert not target.matches(_chunk("07.md", "Return requirements"))  # prefix but not a child
    assert not target.matches(_chunk("08.md", "Return requirement"))


def test_format_reports_marks_misses() -> None:
    results = [
        CaseResult(id="q1", question="a?", hit=True, rank=2, retrieved=[]),
        CaseResult(id="q2", question="b?", hit=False, rank=None, retrieved=[]),
    ]
    report = RetrievalReport(config="hybrid", k=5, hit_at_k=0.5, mrr=0.25, results=results)
    table = format_reports([report])
    assert "| hybrid | 0.50 | 0.25 |" in table
    assert "| q1 a? | 2 |" in table
    assert "| q2 b? | — |" in table
