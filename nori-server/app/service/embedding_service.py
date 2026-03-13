"""
듀얼 벡터DB 임베딩 검색 서비스
1차: ChromaDB (빠른 응답) → AI 검수 → 2차: Milvus (정밀 검색)
"""
import logging
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config.settings import Settings

logger = logging.getLogger("nori-server")


class EmbeddingService:
    """듀얼 벡터DB 검색 서비스 (ChromaDB + Milvus)"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._chroma_client: chromadb.ClientAPI | None = None
        self._collections: dict[str, chromadb.Collection] = {}

    async def initialize(self):
        """서버 시작 시 벡터 DB 연결"""
        try:
            persist_dir = str(self._settings.chroma_persist_path)
            Path(persist_dir).mkdir(parents=True, exist_ok=True)

            self._chroma_client = chromadb.PersistentClient(
                path=persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            logger.info(f"ChromaDB 연결 성공 (persist: {persist_dir})")

            # 기본 컬렉션 초기화
            for name in ("javadoc", "spring", "egov", "community",
                         "web-ui", "desktop-ui", "errors", "custom", "profiles"):
                self._collections[name] = self._chroma_client.get_or_create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"},
                )
            logger.info(f"컬렉션 {len(self._collections)}개 초기화 완료")

        except Exception as e:
            logger.error(f"ChromaDB 초기화 실패: {e}")
            self._chroma_client = None

    @property
    def is_available(self) -> bool:
        return self._chroma_client is not None

    # ── 1차 검색: ChromaDB ──
    async def search(self, query: str, top_k: int = 5,
                     collections: list[str] | None = None,
                     filters: dict | None = None) -> list[dict]:
        """ChromaDB에서 빠른 유사도 검색"""
        if not self.is_available:
            return []

        target_collections = collections or list(self._collections.keys())
        all_results = []

        for col_name in target_collections:
            col = self._collections.get(col_name)
            if not col:
                continue

            try:
                count = col.count()
                if count == 0:
                    continue

                query_params = {
                    "query_texts": [query],
                    "n_results": min(top_k, count),
                }
                if filters:
                    query_params["where"] = filters

                results = col.query(**query_params)

                if results and results.get("documents"):
                    for i, doc in enumerate(results["documents"][0]):
                        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                        distance = results["distances"][0][i] if results.get("distances") else 1.0
                        all_results.append({
                            "text": doc,
                            "metadata": meta,
                            "collection": col_name,
                            "score": 1.0 - distance,
                            "id": results["ids"][0][i] if results.get("ids") else "",
                        })
            except Exception as e:
                logger.warning(f"컬렉션 '{col_name}' 검색 오류: {e}")

        # 점수 기준 정렬
        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results[:top_k]

    # ── 버전/에러 필터 검색 ──
    async def search_by_error(self, error_pattern: str, top_k: int = 5,
                              java_version: str | None = None) -> list[dict]:
        """에러 패턴으로 특화 검색"""
        filters = {}
        if java_version:
            filters["java_version"] = java_version

        results = await self.search(
            query=error_pattern,
            top_k=top_k,
            collections=["errors", "community", "egov"],
            filters=filters if filters else None,
        )
        return results

    async def search_by_version(self, query: str, java_version: str,
                                top_k: int = 5) -> list[dict]:
        """특정 Java 버전으로 필터링된 검색"""
        return await self.search(
            query=query,
            top_k=top_k,
            filters={"java_version": java_version},
        )

    # ── 문서 추가 ──
    async def add_documents(self, collection_name: str,
                            documents: list[str],
                            metadatas: list[dict],
                            ids: list[str]):
        """문서를 벡터 DB에 추가"""
        if not self.is_available:
            raise RuntimeError("ChromaDB가 초기화되지 않았습니다")

        col = self._collections.get(collection_name)
        if not col:
            col = self._chroma_client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self._collections[collection_name] = col

        # 배치 처리 (100개씩)
        batch_size = 100
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i + batch_size]
            batch_metas = metadatas[i:i + batch_size]
            batch_ids = ids[i:i + batch_size]
            col.upsert(documents=batch_docs, metadatas=batch_metas, ids=batch_ids)

        logger.info(f"'{collection_name}'에 {len(documents)}개 문서 추가 완료")

    async def delete_by_metadata(self, collection_name: str, where: dict):
        """메타데이터 조건으로 문서 삭제"""
        if not self.is_available:
            return
        col = self._collections.get(collection_name)
        if not col:
            col = self._chroma_client.get_or_create_collection(
                name=collection_name, metadata={"hnsw:space": "cosine"}
            )
            self._collections[collection_name] = col
        try:
            col.delete(where=where)
        except Exception as e:
            logger.warning("메타데이터 삭제 오류: %s", e)

    async def search_profiles(self, query: str, user_id: str, project_id: str, top_k: int = 5) -> list[dict]:
        """프로필 컬렉션에서 사용자·프로젝트 필터 검색"""
        return await self.search(
            query=query,
            top_k=top_k,
            collections=["profiles"],
            filters={"$and": [{"user_id": user_id}, {"project_id": project_id}]},
        )

    # ── 통계 ──
    async def get_stats(self) -> dict:
        """각 컬렉션별 문서 수 통계"""
        if not self.is_available:
            return {"status": "unavailable"}

        stats = {"status": "connected", "collections": {}}
        for name, col in self._collections.items():
            try:
                stats["collections"][name] = col.count()
            except Exception:
                stats["collections"][name] = -1
        stats["total_documents"] = sum(
            v for v in stats["collections"].values() if v > 0
        )
        return stats
