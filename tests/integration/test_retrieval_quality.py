"""Retrieval quality with the real models: hit@5 on the labelled question set.

Uses the configured (default) local models, so the first run downloads their weights into
.cache/models. Every configuration is reported; the gate applies to the one the Policy agent
uses (hybrid + rerank). Run with -s to see the table locally; in CI it also goes to the job
summary.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.conftest import make_settings

from disputedesk.db import connect
from disputedesk.llm import get_embedder, get_reranker
from disputedesk.rag.evaluation import CONFIGS, evaluate, format_reports, load_cases
from disputedesk.rag.ingest import ingest, load_corpus
from disputedesk.rag.retriever import Retriever

ROOT = Path(__file__).resolve().parents[2]
HIT_AT_5_THRESHOLD = 0.8  # M2 acceptance criterion (docs/PROJECT_BLUEPRINT.md)
GATED_CONFIG = "hybrid + rerank"


@pytest.mark.models
def test_hit_at_5_meets_threshold(database_url: str) -> None:
    settings = make_settings(database_url=database_url)  # defaults: real fastembed models
    embedder = get_embedder(settings)
    reranker = get_reranker(settings)
    assert reranker is not None
    cases = load_cases(ROOT / "evals" / "datasets" / "retrieval.jsonl")

    with connect(database_url) as conn:
        ingest(conn, embedder, load_corpus(ROOT / "data" / "policies"))  # no-op if current
        retriever = Retriever(conn, embedder, reranker)
        reports = {c.name: evaluate(retriever, cases, c, k=5) for c in CONFIGS}

    table = format_reports(list(reports.values()))
    print(f"\nRetrieval quality ({embedder.model}, {reranker.model})\n{table}")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Retrieval quality\n\n{table}\n")

    gated = reports[GATED_CONFIG]
    misses = [f"{r.id}: {r.retrieved}" for r in gated.results if not r.hit]
    assert gated.hit_at_k >= HIT_AT_5_THRESHOLD, f"hit@5 {gated.hit_at_k:.2f}; misses: {misses}"
