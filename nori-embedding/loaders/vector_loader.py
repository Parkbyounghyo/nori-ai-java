"""
청크 Document를 ChromaDB에 적재하는 벡터 로더.
ChromaDB 내장 임베딩(all-MiniLM-L6-v2)을 사용하여 별도 임베딩 API 호출 불필요.
"""
from __future__ import annotations

import logging
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from parsers.json_parser import Document

logger = logging.getLogger("nori-embedding")

# 소스 타입 → ChromaDB 컬렉션 매핑
SOURCE_TO_COLLECTION = {
    "javadoc": "javadoc",
    "spring-doc": "spring",
    "web-ui": "web-ui",
    "desktop-ui": "desktop-ui",
    "egov": "egov",
    "community": "community",
    "community-qa": "community",
    "community-tutorial": "community",
    "community-issue": "community",
    "database": "database",
    "database-doc": "database",
    "oracle-doc": "database",
    "mariadb-doc": "database",
    "mongodb-doc": "database",
    "postgresql-doc": "database",
    "redis-doc": "database",
    "sqlite-doc": "database",
    "errors": "errors",
    "custom": "custom",
}

BATCH_SIZE = 100


class VectorLoader:
    """ChromaDB 벡터 적재기"""

    def __init__(self, chroma_dir: str | Path):
        self._persist_dir = str(Path(chroma_dir).resolve())
        self._client: chromadb.ClientAPI | None = None
        self._collections: dict[str, chromadb.Collection] = {}

    def connect(self):
        """ChromaDB 연결 및 컬렉션 초기화"""
        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info(f"ChromaDB 연결 (persist: {self._persist_dir})")

        # 기본 컬렉션 생성
        for col_name in SOURCE_TO_COLLECTION.values():
            self._collections[col_name] = self._client.get_or_create_collection(
                name=col_name,
                metadata={"hnsw:space": "cosine"},
            )
        logger.info(f"컬렉션 {len(self._collections)}개 준비 완료")

    def _get_collection(self, source_type: str) -> chromadb.Collection | None:
        col_name = SOURCE_TO_COLLECTION.get(source_type)
        if not col_name:
            col_name = "custom"
        return self._collections.get(col_name)

    def load(self, documents: list[Document], *, force: bool = False) -> dict:
        """
        Document 리스트를 ChromaDB에 적재한다.
        동일 ID 문서는 upsert로 덮어쓴다.
        """
        if not self._client:
            raise RuntimeError("connect()를 먼저 호출하세요")

        # 컬렉션별 그룹화
        grouped: dict[str, list[Document]] = {}
        for doc in documents:
            st = doc.metadata.get("source_type", "custom")
            col_name = SOURCE_TO_COLLECTION.get(st, "custom")
            grouped.setdefault(col_name, []).append(doc)

        stats = {"total": 0, "collections": {}}

        for col_name, docs in grouped.items():
            col = self._collections.get(col_name)
            if not col:
                logger.warning(f"컬렉션 '{col_name}' 없음, 스킵")
                continue

            count = 0
            for i in range(0, len(docs), BATCH_SIZE):
                batch = docs[i:i + BATCH_SIZE]
                ids = [d.id for d in batch]
                texts = [d.text for d in batch]
                metas = [self._sanitize_metadata(d.metadata) for d in batch]

                col.upsert(documents=texts, metadatas=metas, ids=ids)
                count += len(batch)

                if count % 500 == 0:
                    logger.info(f"  [{col_name}] {count}/{len(docs)} 적재 중...")

            stats["collections"][col_name] = count
            stats["total"] += count
            logger.info(f"[{col_name}] {count}개 문서 적재 완료")

        return stats

    def get_stats(self) -> dict:
        """전체 컬렉션 통계"""
        if not self._client:
            return {}
        result = {}
        for name, col in self._collections.items():
            result[name] = col.count()
        return result

    @staticmethod
    def _sanitize_metadata(meta: dict) -> dict:
        """ChromaDB 메타데이터 호환을 위해 값을 문자열/수치/불리언으로 정리"""
        clean = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                clean[k] = v
            elif isinstance(v, list):
                clean[k] = ", ".join(str(x) for x in v)
            elif v is not None:
                clean[k] = str(v)
        return clean
