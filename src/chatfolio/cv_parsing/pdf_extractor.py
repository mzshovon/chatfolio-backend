import pymupdf


def extract_text_from_pdf(content: bytes) -> str:
    with pymupdf.open(stream=content, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)
