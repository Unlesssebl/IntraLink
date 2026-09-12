"""Domain contract for text source provenance and exact character spans."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextSourceFragment:
    """Точный фрагмент исходного текста тикета с метаданными авторства и офсетами."""

    field_name: str
    offset_start: int
    offset_end: int
    text: str
    author: str | None = None
    created_at: str | None = None
    source_id: str | None = None

    def span(self) -> tuple[int, int]:
        """Возвращает диапазон символов [offset_start, offset_end]."""
        return self.offset_start, self.offset_end

    def matches_slice(self, raw_text: str) -> bool:
        """Проверяет, совпадает ли текст фрагмента с указанным срезом исходного текста."""
        if not raw_text:
            return False
        if self.offset_start < 0 or self.offset_end > len(raw_text) or self.offset_start > self.offset_end:
            return False
        return raw_text[self.offset_start:self.offset_end] == self.text
