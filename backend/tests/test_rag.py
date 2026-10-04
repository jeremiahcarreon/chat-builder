import docx
import pytest

from app.llm.prompt import build_user_turn
from app.rag.chunking import TARGET_CHARS, chunk_sections
from app.rag.embeddings import document_prompt, query_prompt
from app.rag.parsing import ParseError, Section, extract


def test_chunks_respect_size_and_keep_pages():
    sections = [
        Section("\n\n".join(f"Paragraph {page}-{i}. " + "word " * 60 for i in range(8)), page=page)
        for page in (1, 2, 3)
    ]
    chunks = chunk_sections(sections)
    assert len(chunks) > 3
    assert all(len(c.text) <= TARGET_CHARS + 400 for c in chunks)
    assert chunks[0].page == 1 and chunks[-1].page == 3
    joined = " ".join(c.text for c in chunks)
    for page in (1, 2, 3):
        for i in range(8):
            assert f"Paragraph {page}-{i}." in joined


def test_huge_paragraph_is_split():
    chunks = chunk_sections([Section("x" * 10_000)])
    assert len(chunks) > 4
    assert all(len(c.text) <= TARGET_CHARS + 400 for c in chunks)


def test_small_document_is_one_chunk():
    assert [c.text for c in chunk_sections([Section("short text", 1)])] == ["short text"]


def test_docx_extracts_paragraphs_and_tables(tmp_path):
    document = docx.Document()
    document.add_paragraph("The Gold plan costs $219.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Emergency fee"
    table.cell(0, 1).text = "Waived"
    path = tmp_path / "plans.docx"
    document.save(path)
    text = extract(path)[0].text
    assert "The Gold plan costs $219." in text
    assert "Emergency fee | Waived" in text


def test_html_strips_scripts(tmp_path):
    path = tmp_path / "page.html"
    path.write_text("<html><head><title>x</title></head><body><script>alert(1)</script><p>Hello &amp; welcome</p></body></html>")
    assert extract(path)[0].text == "Hello & welcome"


def test_unsupported_and_empty_files(tmp_path):
    (tmp_path / "a.exe").write_text("x")
    with pytest.raises(ParseError):
        extract(tmp_path / "a.exe")
    (tmp_path / "empty.txt").write_text("   ")
    with pytest.raises(ParseError):
        extract(tmp_path / "empty.txt")


def test_embedding_prompts_use_gemma_task_prefixes():
    assert document_prompt("body", "Manual") == "title: Manual | text: body"
    assert document_prompt("body") == "title: none | text: body"
    assert query_prompt("q") == "task: search result | query: q"


def test_user_turn_wraps_context():
    assert build_user_turn("hi", [], []) == "hi"
    turn = build_user_turn("What is E7?", [("manual.pdf, page 2", "E7 means...")], [])
    assert '<excerpt source="manual.pdf, page 2">' in turn
    assert turn.endswith("<question>\nWhat is E7?\n</question>")
