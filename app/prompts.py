OCR_EXTRACTION_PROMPT = (
    "Extract all text and content from this document page exactly as it appears. "
    "Preserve the original structure: headings hierarchy, tables, bullet points, "
    "numbered lists, and spatial layout. "
    "Output only the extracted content — no commentary, summaries, or additions."
)

DOCUMENT_CLASSIFICATION_PROMPT = (
    "You are classifying a legal document based on its content.\n\n"
    "Classify it as exactly one of:\n"
    "- 'exhibit': a proof document provided by the client as evidence "
    "(e.g. birth certificate, passport, photograph, bank statement, tax return, letter)\n"
    "- 'filed_doc': a form or application filled out by the client for a legal process\n\n"
    "Respond with exactly one word: either 'exhibit' or 'filed_doc'. "
    "No explanation, no punctuation."
)
