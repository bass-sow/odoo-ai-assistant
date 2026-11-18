"""Split documents into chunks that keep their section heading as context."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Chunk:
    id: str
    text: str
    source: str
    title: str

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "source": self.source, "title": self.title}


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) + 2 > max_chars:
            pieces.append(current)
            current = current[-overlap:] + "\n\n" + p if overlap else p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        pieces.append(current)
    # A single huge paragraph: hard split.
    out = []
    for piece in pieces:
        while len(piece) > max_chars:
            out.append(piece[:max_chars])
            piece = piece[max_chars - overlap :]
        out.append(piece)
    return out


def chunk_markdown(text: str, source: str, max_chars: int = 900, overlap: int = 120) -> list[Chunk]:
    doc_title = source
    sections: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            if buf and "".join(buf).strip():
                sections.append((heading, "\n".join(buf).strip()))
            if m.group(1) == "#":
                doc_title = m.group(2).strip()
            heading, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    if buf and "".join(buf).strip():
        sections.append((heading, "\n".join(buf).strip()))

    chunks = []
    for heading, body in sections:
        title = doc_title if heading in ("", doc_title) else f"{doc_title} > {heading}"
        for piece in _split_long(body, max_chars, overlap):
            # The title goes into the embedded text: short chunks need that context.
            text = f"{title}\n\n{piece}"
            cid = hashlib.sha1(f"{source}|{text}".encode()).hexdigest()[:16]
            chunks.append(Chunk(id=cid, text=text, source=source, title=title))
    return chunks


def chunk_pdf(path: Path, max_chars: int = 900, overlap: int = 120) -> list[Chunk]:
    from pypdf import PdfReader

    chunks = []
    for page_no, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        for piece in _split_long(text, max_chars, overlap):
            source = f"{path.name}#page={page_no}"
            cid = hashlib.sha1(f"{source}|{piece}".encode()).hexdigest()[:16]
            chunks.append(Chunk(id=cid, text=piece, source=source, title=f"{path.stem} p.{page_no}"))
    return chunks


def load_documents(docs_dir: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(docs_dir).rglob("*")):
        if path.suffix.lower() in (".md", ".txt"):
            chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), path.name))
        elif path.suffix.lower() == ".pdf":
            chunks.extend(chunk_pdf(path))
    return chunks
