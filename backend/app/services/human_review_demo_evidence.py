from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import fitz


DEMO_EVIDENCE_SOURCE = "DEMO_PDF"
DEMO_EVIDENCE_FILENAME = "human_review_scope_demo.pdf"
DEMO_EVIDENCE_SHA256 = "9880fccb6e380f788d4f8be27e3f7a221e412ad1b805240d6bf1de9e24b32381"
DEMO_EVIDENCE_SIZE = 2896
DEMO_EVIDENCE_PAGES = 2
_DEMO_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "demo"
    / DEMO_EVIDENCE_FILENAME
)


def load_demo_evidence_pdf() -> bytes:
    expected = _DEMO_EVIDENCE_PATH.resolve(strict=True)
    demo_root = (Path(__file__).resolve().parents[2] / "data" / "demo").resolve()
    if expected.parent != demo_root or expected.name != DEMO_EVIDENCE_FILENAME:
        raise ValueError("demo evidence path is invalid")
    content = expected.read_bytes()
    if (
        expected.suffix.casefold() != ".pdf"
        or len(content) != DEMO_EVIDENCE_SIZE
        or not content.startswith(b"%PDF-")
        or sha256(content).hexdigest() != DEMO_EVIDENCE_SHA256
    ):
        raise ValueError("demo evidence integrity check failed")
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.needs_pass or document.page_count != DEMO_EVIDENCE_PAGES:
                raise ValueError("demo evidence page contract failed")
    except RuntimeError as error:
        raise ValueError("demo evidence PDF is invalid") from error
    return content
