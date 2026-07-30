"""Converts a PDF file into an editable .docx, aiming to preserve layout,
text formatting, and tables as closely as possible.

Uses pdf2docx, which parses the PDF's own embedded text/vector content —
it does not OCR scanned/image-only pages, so those come through as
embedded images rather than editable text.

This CLI is a thin wrapper around app.services.shared.pdf_conversion —
the same conversion the template generation pipeline uses for .pdf uploads.
Run manually:

    python convert_pdf_to_docx.py <input.pdf> <output.docx>
"""
import sys

from app.services.shared.pdf_conversion import pdf_bytes_to_docx_bytes


def convert_pdf_to_docx(pdf_path: str, docx_path: str) -> None:
    print(f"Converting '{pdf_path}' -> '{docx_path}'...")
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    docx_bytes = pdf_bytes_to_docx_bytes(pdf_bytes)
    with open(docx_path, "wb") as f:
        f.write(docx_bytes)
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python convert_pdf_to_docx.py <input.pdf> <output.docx>")
        sys.exit(1)
    convert_pdf_to_docx(sys.argv[1], sys.argv[2])
