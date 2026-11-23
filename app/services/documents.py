from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader


class UnsupportedDocumentType(ValueError):
    pass


class DocumentParser:
    text_suffixes = {".txt", ".md", ".csv", ".json"}

    def parse(self, filename: str, data: bytes) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix in self.text_suffixes:
            return self._decode_text(data)
        if suffix == ".pdf":
            reader = PdfReader(BytesIO(data))
            return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        if suffix == ".docx":
            return self._parse_docx(data)
        raise UnsupportedDocumentType(f"unsupported document type: {suffix or 'unknown'}")

    @staticmethod
    def _decode_text(data: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return data.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        raise ValueError("unable to decode text document")

    @staticmethod
    def _parse_docx(data: bytes) -> str:
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        try:
            with ZipFile(BytesIO(data)) as archive:
                xml = archive.read("word/document.xml")
        except (BadZipFile, KeyError) as exc:
            raise ValueError("invalid DOCX document") from exc
        root = ElementTree.fromstring(xml)
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", namespace):
            parts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
            text = "".join(parts).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs).strip()

