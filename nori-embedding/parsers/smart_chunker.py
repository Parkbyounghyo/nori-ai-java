"""
의미 단위 텍스트 청커.
큰 Document를 임베딩에 적합한 크기(~512토큰)로 분할한다.
오버랩(50토큰)으로 문맥 유지.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass

from parsers.json_parser import Document

logger = logging.getLogger("nori-embedding")

# 대략적 토큰 추정: 영문 1토큰 ≈ 4글자, 한글 1토큰 ≈ 1.5글자
# 보수적으로 1토큰 ≈ 3글자 기준
_CHARS_PER_TOKEN = 3

DEFAULT_MAX_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 50


@dataclass
class ChunkConfig:
    max_tokens: int = DEFAULT_MAX_TOKENS
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS

    @property
    def max_chars(self) -> int:
        return self.max_tokens * _CHARS_PER_TOKEN

    @property
    def overlap_chars(self) -> int:
        return self.overlap_tokens * _CHARS_PER_TOKEN


def _split_by_paragraphs(text: str) -> list[str]:
    """텍스트를 문단 단위로 분할한다."""
    # 빈 줄 기준 분할, 연속 빈줄은 하나로
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _merge_small_chunks(paragraphs: list[str], max_chars: int, overlap_chars: int) -> list[str]:
    """
    작은 문단들을 합쳐서 max_chars 이내의 청크로 만든다.
    큰 문단은 문장 단위로 재분할한다.
    """
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        # 문단 자체가 max_chars보다 큰 경우 → 문장 단위 분할
        if len(para) > max_chars:
            # 현재 버퍼 플러시
            if current:
                chunks.append(current)
                current = ""

            sentences = re.split(r"(?<=[.!?。])\s+", para)
            buf = ""
            for sent in sentences:
                if len(buf) + len(sent) + 1 <= max_chars:
                    buf = f"{buf} {sent}".strip() if buf else sent
                else:
                    if buf:
                        chunks.append(buf)
                    # 문장 하나가 max_chars보다 길면 강제 분할
                    if len(sent) > max_chars:
                        for i in range(0, len(sent), max_chars - overlap_chars):
                            chunks.append(sent[i:i + max_chars])
                    else:
                        buf = sent
            if buf:
                chunks.append(buf)
            continue

        # 합치면 max_chars 초과 → 현재 버퍼 플러시
        if len(current) + len(para) + 2 > max_chars:
            if current:
                chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}".strip() if current else para

    if current:
        chunks.append(current)

    return chunks


def _add_overlap(chunks: list[str], overlap_chars: int) -> list[str]:
    """인접 청크 간 오버랩을 추가한다."""
    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks

    result = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1][-overlap_chars:]
        result.append(f"{prev_tail}\n…\n{chunks[i]}")
    return result


def chunk_document(doc: Document, config: ChunkConfig | None = None) -> list[Document]:
    """
    하나의 Document를 적절한 크기의 청크 Document 리스트로 분할한다.
    이미 max_chars 이내이면 그대로 반환한다.
    """
    cfg = config or ChunkConfig()

    text = doc.text.strip()
    if not text:
        return []

    # 이미 작은 문서면 분할 불필요
    if len(text) <= cfg.max_chars:
        return [doc]

    paragraphs = _split_by_paragraphs(text)
    raw_chunks = _merge_small_chunks(paragraphs, cfg.max_chars, cfg.overlap_chars)
    chunks_with_overlap = _add_overlap(raw_chunks, cfg.overlap_chars)

    docs: list[Document] = []
    for i, chunk_text in enumerate(chunks_with_overlap):
        docs.append(Document(
            id=f"{doc.id}:chunk:{i}",
            text=chunk_text,
            metadata={
                **doc.metadata,
                "chunk_index": i,
                "total_chunks": len(chunks_with_overlap),
                "parent_id": doc.id,
            },
        ))

    return docs


def chunk_documents(docs: list[Document], config: ChunkConfig | None = None) -> list[Document]:
    """Document 리스트 전체를 청킹한다."""
    cfg = config or ChunkConfig()
    all_chunks: list[Document] = []

    for doc in docs:
        all_chunks.extend(chunk_document(doc, cfg))

    logger.info(f"청킹 완료: {len(docs)}개 원문 → {len(all_chunks)}개 청크")
    return all_chunks
