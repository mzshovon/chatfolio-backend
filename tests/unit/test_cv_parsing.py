import pytest

from chatfolio.cv_parsing import CVParsingError, extract_text
from chatfolio.cv_parsing.docx_extractor import extract_text_from_docx
from chatfolio.cv_parsing.pdf_extractor import extract_text_from_pdf
from tests.factories.documents import make_docx_bytes, make_pdf_bytes


def test_extract_text_from_pdf() -> None:
    content = make_pdf_bytes("Jane Doe - Backend Engineer")
    assert "Jane Doe" in extract_text_from_pdf(content)


def test_extract_text_from_docx() -> None:
    content = make_docx_bytes("Jane Doe - Backend Engineer")
    assert "Jane Doe" in extract_text_from_docx(content)


def test_extract_text_dispatches_pdf() -> None:
    content = make_pdf_bytes("hello world")
    assert "hello world" in extract_text("pdf", content)


def test_extract_text_dispatches_docx() -> None:
    content = make_docx_bytes("hello world")
    assert "hello world" in extract_text("docx", content)


def test_extract_text_rejects_legacy_doc() -> None:
    with pytest.raises(CVParsingError, match="doc"):
        extract_text("doc", b"irrelevant")


def test_extract_text_rejects_unknown_type() -> None:
    with pytest.raises(CVParsingError):
        extract_text("exe", b"irrelevant")
