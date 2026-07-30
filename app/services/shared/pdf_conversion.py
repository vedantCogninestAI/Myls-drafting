import io

from pdf2docx import Converter


def pdf_bytes_to_docx_bytes(pdf_bytes: bytes) -> bytes:
    """Converts a PDF's bytes into an editable .docx's bytes, in memory.

    Uses pdf2docx, which parses the PDF's own embedded text/vector content —
    it does not OCR scanned/image-only pages, so those come through as
    embedded images rather than editable text.
    """
    buffer = io.BytesIO()
    converter = Converter(stream=pdf_bytes)
    try:
        converter.convert(buffer)
    finally:
        converter.close()
    return buffer.getvalue()
