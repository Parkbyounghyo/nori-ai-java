"""
듀얼 벡터DB 임베딩 검색 서비스
ChromaDB vector search (top 20 fetch) → re-rank → top_k 반환
"""
import logging
import re
from pathlib import Path
from collections import Counter

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config.settings import Settings

logger = logging.getLogger("nori-server")

# 내부 페치 수: 카이에서 rerank 후 top_k 문서 반환
VECTOR_FETCH_K = 20
_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

_KO_SEARCH_STOPWORDS = {
    "관련", "알려줘", "어떻게", "뽐야", "좌", "요즘", "최근", "어떤",
    "있나요", "있어", "있을까", "알고싶어", "설명해", "궁금",
    "뭐야", "뭐지", "어디서", "언제", "우리", "저희",
}


class EmbeddingService:
    """ChromaDB 벡터 검색 서비스 (fetch top-20 → rerank → top_k)"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._chroma_client: chromadb.ClientAPI | None = None
        self._collections: dict[str, chromadb.Collection] = {}
        self._reranker = None
        self._reranker_loaded = False

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
                         "web-ui", "desktop-ui", "errors", "custom",
                         "project-templates", "profiles", "database"):
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

    # ── Reranker lazy load ──
    def _get_reranker(self):
        if self._reranker_loaded:
            return self._reranker
        try:
            from sentence_transformers import CrossEncoder
            self._reranker = CrossEncoder(_RERANKER_MODEL)
            logger.info(f"Reranker 로드 완료: {_RERANKER_MODEL}")
        except Exception as e:
            logger.warning(f"Reranker 로드 실패 (점수 기반 정렬 사용): {e}")
            self._reranker = None
        self._reranker_loaded = True
        return self._reranker

    def _rerank(self, query: str, documents: list[dict], top_n: int) -> list[dict]:
        """reranker로 정렬. 미설치 시 점수 기반 정렬 폴백."""
        reranker = self._get_reranker()
        if not reranker or not documents:
            return documents[:top_n]
        try:
            pairs = [(query, doc["text"]) for doc in documents]
            scores = reranker.predict(pairs)
            ranked = sorted(zip(documents, scores), key=lambda x: float(x[1]), reverse=True)
            return [doc for doc, _ in ranked[:top_n]]
        except Exception as e:
            logger.warning(f"Rerank 실패 (점수 기반 정렬 사용): {e}")
            return documents[:top_n]

    def normalize_query(self, query: str) -> dict:
        """자연어 쿼리를 벡터 검색용으로 정규화. vector_query / keyword_filter 반환."""
        ko_words = re.findall(r"[\uAC00-\uD7A3]{2,}", query)
        en_words = re.findall(r"[a-zA-Z]{2,}", query)
        keywords = [w for w in ko_words if w not in _KO_SEARCH_STOPWORDS] + en_words
        # 중복 제거 순서 유지
        seen: list[str] = []
        for w in keywords:
            if w not in seen:
                seen.append(w)
        vector_query = " ".join(seen) if seen else query
        return {
            "vector_query": vector_query,
            "keyword_filter": seen[:5],
            "original_query": query,
        }

    # ── 1차 검색: ChromaDB (fetch top-20 → rerank → top_k) ──
    async def search(self, query: str, top_k: int = 5,
                     collections: list[str] | None = None,
                     filters: dict | None = None) -> list[dict]:
        """ChromaDB vector search → rerank → top_k 반환

        내부적으로 VECTOR_FETCH_K(20)개를 마우고 rerank 후 top_k를 반환한다.
        """
        if not self.is_available:
            return []

        fetch_k = max(top_k * 4, VECTOR_FETCH_K)
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
                    "n_results": min(fetch_k, count),
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

        # 1차 점수 정렬 후 rerank
        all_results.sort(key=lambda x: x["score"], reverse=True)
        candidates = all_results[:fetch_k]
        return self._rerank(query, candidates, top_k)

    # ── 프로젝트 전용 검색 메서드 ──
    async def search_project(self, query: str, top_k: int = 5,
                             project_id: str | None = None) -> list[dict]:
        """업무 코드 컬렉션(custom)만 검색. 템플릿 제외."""
        filters = {"project": project_id} if project_id else None
        return await self.search(
            query=query, top_k=top_k,
            collections=["custom"], filters=filters,
        )

    async def search_template(self, query: str, top_k: int = 5,
                              project_id: str | None = None) -> list[dict]:
        """템플릿/에디터 자산 컬렉션(project-templates) 검색."""
        filters = {"project": project_id} if project_id else None
        return await self.search(
            query=query, top_k=top_k,
            collections=["project-templates"], filters=filters,
        )

    # ── 버전/에러/솔루션 필터 검색 ──
    async def search_by_solution(self, query: str, solution: str,
                                 doc_role: str | None = None,
                                 top_k: int = 5) -> list[dict]:
        """UI 솔루션 라이브러리(CKEditor, SmartEditor 등)로 필터링된 검색."""
        filters: dict = {"solution": solution}
        if doc_role:
            filters["doc_role"] = doc_role
        return await self.search(
            query=query,
            top_k=top_k,
            collections=["web-ui", "desktop-ui"],
            filters=filters,
        )

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
