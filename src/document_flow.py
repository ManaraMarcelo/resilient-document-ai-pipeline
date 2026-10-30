from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PreparedDocument:
    page_text: tuple[str, ...]
    content_hash: str

    @property
    def has_usable_text(self) -> bool:
        non_empty = sum(bool(text.strip()) for text in self.page_text)
        return non_empty == len(self.page_text) and sum(map(len, self.page_text)) > 100


class NativeExtractor(Protocol):
    def extract(self, pdf_bytes: bytes) -> PreparedDocument: ...


class VisualReader(Protocol):
    def read_pages(self, pdf_bytes: bytes, page_count: int) -> list[tuple[int, str]]: ...


def prepare_with_recovery(
    pdf_bytes: bytes,
    native: NativeExtractor,
    visual: VisualReader,
) -> tuple[PreparedDocument, dict[str, object]]:
    """Return native text when usable; otherwise recover page text visually."""
    document = native.extract(pdf_bytes)
    audit: dict[str, object] = {"text_source": "native", "visual_fallback": False}

    if document.has_usable_text:
        return document, audit

    pages = visual.read_pages(pdf_bytes, page_count=len(document.page_text))
    expected = list(range(1, len(document.page_text) + 1))
    received = [number for number, text in pages if isinstance(number, int) and text.strip()]
    if received != expected:
        raise ValueError("Visual recovery did not return one non-empty text value per page")

    recovered = PreparedDocument(
        page_text=tuple(text for _, text in pages),
        content_hash=document.content_hash,
    )
    return recovered, {
        "text_source": "visual_recovery",
        "visual_fallback": True,
        "page_count": len(pages),
    }
