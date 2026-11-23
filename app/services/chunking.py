import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChunk:
    index: int
    content: str
    section_title: str | None
    token_count: int


class TextChunker:
    """Paragraph-aware chunker.

    Chinese text has no reliable whitespace token boundary. For capacity planning we
    approximate one token as two CJK characters or four latin characters.
    """

    heading_pattern = re.compile(r"^(#{1,6}\s+.+|[一二三四五六七八九十]+[、.]\s*.+|\d+[.、]\s*.+)$")

    def __init__(self, target_chars: int = 900, overlap_chars: int = 120) -> None:
        if target_chars < 100:
            raise ValueError("target_chars must be at least 100")
        if overlap_chars < 0 or overlap_chars >= target_chars:
            raise ValueError("overlap_chars must be between 0 and target_chars")
        self.target_chars = target_chars
        self.overlap_chars = overlap_chars

    def split(self, text: str) -> list[TextChunk]:
        normalized = self._normalize(text)
        if not normalized:
            return []

        paragraphs = [part.strip() for part in normalized.split("\n") if part.strip()]
        chunks: list[TextChunk] = []
        buffer: list[str] = []
        buffer_length = 0
        current_heading: str | None = None

        def flush() -> None:
            nonlocal buffer, buffer_length
            if not buffer:
                return
            content = "\n".join(buffer).strip()
            chunks.append(
                TextChunk(
                    index=len(chunks),
                    content=content,
                    section_title=current_heading,
                    token_count=self._estimate_tokens(content),
                )
            )
            overlap = content[-self.overlap_chars :].strip() if self.overlap_chars else ""
            buffer = [overlap] if overlap else []
            buffer_length = len(overlap)

        for paragraph in paragraphs:
            if self.heading_pattern.match(paragraph) and len(paragraph) <= 120:
                if buffer:
                    flush()
                    buffer = []
                    buffer_length = 0
                current_heading = paragraph.lstrip("# ").strip()

            if len(paragraph) > self.target_chars:
                if buffer:
                    flush()
                start = 0
                while start < len(paragraph):
                    end = min(start + self.target_chars, len(paragraph))
                    part = paragraph[start:end].strip()
                    if part:
                        chunks.append(
                            TextChunk(
                                index=len(chunks),
                                content=part,
                                section_title=current_heading,
                                token_count=self._estimate_tokens(part),
                            )
                        )
                    if end == len(paragraph):
                        break
                    start = max(end - self.overlap_chars, start + 1)
                buffer = []
                buffer_length = 0
                continue

            projected = buffer_length + len(paragraph) + (1 if buffer else 0)
            if buffer and projected > self.target_chars:
                flush()
            buffer.append(paragraph)
            buffer_length += len(paragraph) + (1 if buffer_length else 0)

        flush()
        return [
            TextChunk(
                index=index,
                content=chunk.content,
                section_title=chunk.section_title,
                token_count=chunk.token_count,
            )
            for index, chunk in enumerate(chunks)
        ]

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
        other_count = max(0, len(text) - cjk_count)
        return max(1, (cjk_count + 1) // 2 + (other_count + 3) // 4)

