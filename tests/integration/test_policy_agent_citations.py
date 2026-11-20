"""Every Policy agent answer carries at least one citation, over the whole retrieval set.

Real retriever (fake models, own table) and real Postgres audit_log; the LLM is a
deterministic stand-in that quotes the top excerpt, so this checks the wiring and the citation
guarantee, not answer quality (that is an M7 eval with a real model).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

import psycopg
import pytest

from disputedesk.agents.policy import ACTOR, PolicyAgent
from disputedesk.db import connect
from disputedesk.llm import FakeClient, FakeEmbedder, FakeReranker, Message
from disputedesk.mcp_servers.common.store import PostgresStore
from disputedesk.rag.evaluation import load_cases
from disputedesk.rag.ingest import ingest, load_corpus
from disputedesk.rag.retriever import Retriever

ROOT = Path(__file__).resolve().parents[2]
TABLE = "policy_chunks_agent_test"
_EXCERPT_1 = re.compile(r'<excerpt id="1" [^>]*>\n(.*?)\n</excerpt>', re.DOTALL)


def quote_first_excerpt(system: str, messages: Sequence[Message]) -> str:
    match = _EXCERPT_1.search(messages[0].content)
    assert match is not None
    first_line = match.group(1).splitlines()[0]
    citation = {"excerpt_id": 1, "quote": first_line[:200]}
    return json.dumps({"summary": "See the cited rule.", "citations": [citation]})


@pytest.fixture
def rag_conn(database_url: str) -> Iterator[psycopg.Connection]:
    with connect(database_url) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
        ingest(conn, FakeEmbedder(), load_corpus(ROOT / "data" / "policies"), table=TABLE)
        yield conn
        conn.execute(f"DROP TABLE IF EXISTS {TABLE}")


def test_every_answer_has_a_citation_and_an_audit_row(
    rag_conn: psycopg.Connection, database_url: str
) -> None:
    cases = load_cases(ROOT / "evals" / "datasets" / "retrieval.jsonl")
    retriever = Retriever(rag_conn, FakeEmbedder(), FakeReranker(), table=TABLE)
    with connect(database_url) as audit_conn:
        store = PostgresStore(audit_conn)
        agent = PolicyAgent(retriever, FakeClient(responder=quote_first_excerpt), store)
        before = _audit_rows(audit_conn)
        for case in cases:
            answer = agent.answer(case.question, case_id="CASE-0001")
            assert answer.citations, case.id
            assert all(c.source.endswith(".md") and c.section for c in answer.citations)
        assert _audit_rows(audit_conn) - before == len(cases)


def _audit_rows(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT count(*) FROM audit_log WHERE actor = %s", (ACTOR,)).fetchone()
    assert row is not None
    return int(row[0])
