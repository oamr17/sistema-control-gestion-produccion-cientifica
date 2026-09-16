from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
from time import perf_counter

import fitz
import pytesseract
from PIL import Image


MIN_DIGITAL_CHARS = 40


@dataclass(slots=True)
class PageExtractionLog:
    page: int
    method: str
    chars: int
    requires_review: bool = False
    warning: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class PdfTextExtraction:
    text: str
    provider: str
    issue: str | None
    page_logs: list[PageExtractionLog]
    page_count: int = 0
    used_ocr: bool = False
    digital_text_ms: int = 0
    ocr_ms: int = 0

    def page_logs_dict(self) -> list[dict]:
        return [item.to_dict() for item in self.page_logs]


def _has_enough_text(text: str | None) -> bool:
    clean = " ".join((text or "").split())
    alnum = sum(char.isalnum() for char in clean)
    return len(clean) >= MIN_DIGITAL_CHARS and alnum >= MIN_DIGITAL_CHARS // 2


def _ocr_page(page: fitz.Page) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    image = Image.open(BytesIO(pixmap.tobytes("png")))
    return pytesseract.image_to_string(image, lang="spa+eng")


def _acroform_text(page: fitz.Page) -> str:
    widgets = list(page.widgets() or [])
    lines: list[str] = []
    for widget in widgets:
        value = str(getattr(widget, "field_value", "") or "").strip()
        if not value:
            continue
        label = str(getattr(widget, "field_label", "") or getattr(widget, "field_name", "") or "CAMPO").strip()
        lines.append(f"{label}: {value}")
    if not lines:
        return ""
    return "CAMPOS DE FORMULARIO\n" + "\n".join(lines)


def extract_pdf_text(content: bytes) -> PdfTextExtraction:
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        return PdfTextExtraction(
            text="",
            provider="PyMuPDF",
            issue=f"El archivo PDF no pudo abrirse correctamente: {exc}",
            page_logs=[],
            page_count=0,
        )

    parts: list[str] = []
    logs: list[PageExtractionLog] = []
    used_ocr = False
    issues: list[str] = []

    page_count = document.page_count
    digital_text_ms = 0
    ocr_ms = 0

    for page_index, page in enumerate(document, start=1):
        digital_started = perf_counter()
        digital_text = page.get_text("text") or ""
        form_text = _acroform_text(page)
        if form_text:
            digital_text = f"{digital_text}\n{form_text}".strip()
        digital_text_ms += int((perf_counter() - digital_started) * 1000)
        if _has_enough_text(digital_text):
            clean = digital_text.strip()
            parts.append(f"[[PAGE_BREAK:{page_index}]]\n{clean}")
            method = "PyMuPDF+AcroForm" if form_text else "PyMuPDF"
            logs.append(PageExtractionLog(page=page_index, method=method, chars=len(clean)))
            continue

        try:
            ocr_started = perf_counter()
            ocr_text = _ocr_page(page).strip()
            ocr_ms += int((perf_counter() - ocr_started) * 1000)
            used_ocr = True
        except pytesseract.TesseractNotFoundError:
            warning = "Tesseract OCR no esta instalado o no esta disponible en el contenedor."
            issues.append(warning)
            logs.append(PageExtractionLog(page=page_index, method="OCR_FAILED", chars=0, requires_review=True, warning=warning))
            continue
        except Exception as exc:
            warning = f"El OCR no pudo procesar la pagina {page_index}: {exc}"
            issues.append(warning)
            logs.append(PageExtractionLog(page=page_index, method="OCR_FAILED", chars=0, requires_review=True, warning=warning))
            continue

        if ocr_text:
            parts.append(f"[[PAGE_BREAK:{page_index}]]\n{ocr_text}")
            logs.append(PageExtractionLog(page=page_index, method="Tesseract", chars=len(ocr_text)))
        else:
            warning = "Pagina sin texto digital suficiente y OCR sin resultado."
            logs.append(PageExtractionLog(page=page_index, method="Tesseract", chars=0, requires_review=True, warning=warning))

    provider = "PyMuPDF+Tesseract" if used_ocr else "PyMuPDF"
    text = "\n".join(part for part in parts if part).strip()
    issue = " | ".join(dict.fromkeys(issues)) if issues else None
    return PdfTextExtraction(
        text=text,
        provider=provider,
        issue=issue,
        page_logs=logs,
        page_count=page_count,
        used_ocr=used_ocr,
        digital_text_ms=digital_text_ms,
        ocr_ms=ocr_ms,
    )
