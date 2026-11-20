from pathlib import Path

import pytest

from disputedesk.rag.ingest import IngestError, chunk_markdown, load_corpus, to_pgvector

POLICIES = Path(__file__).resolve().parents[2] / "data" / "policies"

DOC = """# R20: Merchandise Not Received

> **Fictional policy.** Written for the demo.
> Not real rules.

## When R20 applies

Physical goods only.

## Delivery proof

Proof must come from an approved carrier.

### Signature confirmation above $250

Tracking alone is not conclusive.

## Decision rules

- No shipment: accept.
"""


def test_sections_follow_heading_path() -> None:
    chunks = chunk_markdown(DOC, "04-r20.md")
    assert [c.section for c in chunks] == [
        "When R20 applies",
        "Delivery proof",
        "Delivery proof > Signature confirmation above $250",
        "Decision rules",
    ]
    assert {c.doc_title for c in chunks} == {"R20: Merchandise Not Received"}
    assert {c.source for c in chunks} == {"04-r20.md"}
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3]


def test_chunks_do_not_cross_headings() -> None:
    chunks = chunk_markdown(DOC, "04-r20.md")
    by_section = {c.section: c.content for c in chunks}
    assert by_section["Delivery proof"] == "Proof must come from an approved carrier."
    assert "Tracking alone" not in by_section["Delivery proof"]


def test_disclaimer_banner_is_dropped() -> None:
    chunks = chunk_markdown(DOC, "04-r20.md")
    assert not any("Fictional policy" in c.content for c in chunks)


def test_closing_hashes_are_stripped_only_after_whitespace() -> None:
    text = "# T\n\n## Tier C#\n\na\n\n## Closed ##\n\nb\n\n### Level 2 ###\n\nc\n"
    assert [c.section for c in chunk_markdown(text, "t.md")] == [
        "Tier C#",
        "Closed",
        "Closed > Level 2",
    ]


def test_other_blockquotes_are_kept() -> None:
    text = "# T\n\n## Formula\n\n> refund = disputed amount - refunds already made\n"
    (chunk,) = chunk_markdown(text, "t.md")
    assert chunk.content.startswith("> refund =")


def test_text_before_first_subheading_is_introduction() -> None:
    text = "# Time Limits\n\nThis document is authoritative.\n\n## Merchant deadlines\n\n30 days.\n"
    chunks = chunk_markdown(text, "08.md")
    assert [(c.section, c.content) for c in chunks] == [
        ("Introduction", "This document is authoritative."),
        ("Merchant deadlines", "30 days."),
    ]


def test_heading_level_going_back_up_resets_the_path() -> None:
    text = "# T\n\n## A\n\na\n\n### A1\n\na1\n\n#### A1x\n\nx\n\n## B\n\nb\n\n### B1\n\nb1\n"
    sections = [c.section for c in chunk_markdown(text, "t.md")]
    assert sections == ["A", "A > A1", "A > A1 > A1x", "B", "B > B1"]


def test_headings_inside_code_fences_are_ignored() -> None:
    text = "# T\n\n## Example\n\n```\n## not a heading\n```\n\nafter\n"
    (chunk,) = chunk_markdown(text, "t.md")
    assert chunk.section == "Example"
    assert "## not a heading" in chunk.content
    assert chunk.content.endswith("after")


def test_missing_title_falls_back_to_file_stem() -> None:
    (chunk,) = chunk_markdown("## Only section\n\nbody\n", "no-title.md")
    assert chunk.doc_title == "no-title"


def test_empty_sections_produce_no_chunks() -> None:
    chunks = chunk_markdown("# T\n\n## Empty\n\n## Full\n\ntext\n", "t.md")
    assert [c.section for c in chunks] == ["Full"]


def test_long_sections_split_on_paragraphs_and_keep_section() -> None:
    paragraphs = [f"Paragraph {i}" + " word" * 60 for i in range(6)]  # ~320 chars each
    text = "# T\n\n## Long\n\n" + "\n\n".join(paragraphs) + "\n"
    chunks = chunk_markdown(text, "t.md", max_chars=700)
    assert len(chunks) == 3
    assert all(c.section == "Long" for c in chunks)
    assert all(len(c.content) <= 700 for c in chunks)
    assert "\n\n".join(c.content for c in chunks) == "\n\n".join(paragraphs)


def test_oversized_single_paragraph_is_kept_whole() -> None:
    table = "\n".join(f"| row {i} | value |" for i in range(50))
    (chunk,) = chunk_markdown(f"# T\n\n## Table\n\n{table}\n", "t.md", max_chars=200)
    assert chunk.content == table


def test_hash_changes_with_content_and_section_and_is_stable() -> None:
    a = chunk_markdown("# T\n\n## S\n\nbody\n", "t.md")[0]
    assert a.content_hash == chunk_markdown("# T\n\n## S\n\nbody\n", "t.md")[0].content_hash
    assert a.content_hash != chunk_markdown("# T\n\n## S\n\nbody!\n", "t.md")[0].content_hash
    assert a.content_hash != chunk_markdown("# T\n\n## S2\n\nbody\n", "t.md")[0].content_hash


def test_embedding_text_carries_title_and_section() -> None:
    chunk = chunk_markdown(DOC, "04-r20.md")[2]
    assert chunk.embedding_text.startswith(
        "R20: Merchandise Not Received\nDelivery proof > Signature confirmation above $250\n\n"
    )


def test_policy_corpus_chunks_cleanly() -> None:
    chunks = load_corpus(POLICIES)
    assert len({c.source for c in chunks}) == 15
    assert all(c.section and c.content for c in chunks)
    assert not any("Fictional policy" in c.content for c in chunks)
    keys = [(c.source, c.chunk_index) for c in chunks]
    assert len(keys) == len(set(keys))


def test_empty_corpus_is_refused(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="no markdown files"):
        load_corpus(tmp_path)


def test_to_pgvector_literal() -> None:
    assert to_pgvector([0.5, -1.0, 0.0]) == "[0.5,-1,0]"
