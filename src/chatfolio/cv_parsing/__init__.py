from chatfolio.cv_parsing.docx_extractor import extract_text_from_docx
from chatfolio.cv_parsing.pdf_extractor import extract_text_from_pdf


class CVParsingError(Exception):
    """Raised for any CV that cannot be turned into text (unsupported/legacy format)."""


def extract_text(file_type: str, content: bytes) -> str:
    if file_type == "pdf":
        return extract_text_from_pdf(content)
    if file_type == "docx":
        return extract_text_from_docx(content)
    if file_type == "doc":
        raise CVParsingError(
            "Legacy .doc format is not supported for automatic parsing. "
            "Please re-upload as PDF or DOCX."
        )
    raise CVParsingError(f"Unsupported file type: {file_type!r}")
