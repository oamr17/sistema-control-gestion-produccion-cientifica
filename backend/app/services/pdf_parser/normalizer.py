from __future__ import annotations

import re
import unicodedata


MOJIBAKE_REPLACEMENTS = {
    "Ã¡": "á",
    "Ã©": "é",
    "Ã­": "í",
    "Ã³": "ó",
    "Ãº": "ú",
    "Ã±": "ñ",
    "Ã": "Á",
    "Ã‰": "É",
    "Ã": "Í",
    "Ã“": "Ó",
    "Ãš": "Ú",
    "Ã‘": "Ñ",
    "ï¿½": "",
    "\ufffd": "",
}


def clean_text(value: object) -> str:
    text = str(value or "")
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    text = text.replace("\x00", "")
    text = "".join(char for char in text if char in ("\n", "\r", "\t", "\f") or ord(char) >= 32)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_key(value: object) -> str:
    text = clean_text(value).upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^A-Z0-9Ñ%./:\- ]+", " ", text)
    return " ".join(text.split())


def normalize_line(value: object) -> str:
    return " ".join(clean_text(value).split()).strip()


def split_lines(text: str | None) -> list[str]:
    cleaned = clean_text(text).replace("\f", "\n[[PAGE_BREAK]]\n")
    lines: list[str] = []
    for item in cleaned.splitlines():
        stripped = item.strip()
        line = (
            stripped
            if re.fullmatch(r"\[\[PAGE_BREAK(?::\d+)?\]\]", stripped, flags=re.IGNORECASE)
            else normalize_line(item)
        )
        if line:
            lines.append(line)
    return lines


def looks_like_url(value: str | None) -> bool:
    text = clean_text(value).lower()
    return text.startswith(("http", "www.")) or "doi.org" in text or ".com" in text or ".org" in text


def rebuild_url(parts: list[str]) -> str | None:
    joined = "".join(part.strip() for part in parts if part)
    joined = joined.replace(" ", "")
    joined = joined.replace("https//", "https://").replace("http//", "http://")
    return joined or None
