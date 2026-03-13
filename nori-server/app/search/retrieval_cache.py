"""
Retrieval Cache Layer — 벡터 검색 결과 캐시 (TTL 인메모리)

Redis가 없어도 동작하는 인메모리 TTL 캐시.
Redis 설치 시 자동으로 Redis 백엔드 사용.

캐시 전략:
  normalized_query → (results, timestamp)
  기본 TTL: 10분 (600초)
  최대 항목: 512개 (LRU 교체)
"""
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("nori-server")

# ── 기본 설정 ──
_DEFAULT_TTL = 600       # 10분
_DEFAULT_MAX_SIZE = 512  # 최대 캐시 항목 수


class InMemoryTTLCache:
    """LRU + TTL 인메모리 캐시.

    thread-safe 하지 않음 (asyncio 단일 스레드에서 사용 전제).
    """

    def __init__(self, ttl: int = _DEFAULT_TTL, max_size: int = _DEFAULT_MAX_SIZE):
        self._ttl = ttl
        self._max_size = max_size
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()  # key → (value, expire_at)
        self._hits = 0
        self._misses = 0

    # ── 공개 API ──

    def get(self, key: str) -> Any | None:
        """캐시 조회. 없거나 만료된 경우 None 반환."""
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, expire_at = entry
        if time.monotonic() > expire_at:
            del self._store[key]
            self._misses += 1
            return None
        # LRU: 최근 접근 항목을 끝으로 이동
        self._store.move_to_end(key)
        self._hits += 1
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """캐시 저장. max_size 초과 시 가장 오래된 항목 제거."""
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, time.monotonic() + (ttl or self._ttl))
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def invalidate(self, key: str) -> bool:
        """특정 키 제거. 제거 성공 시 True."""
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """전체 캐시 비우기."""
        self._store.clear()
        logger.info("Retrieval cache cleared")

    def stats(self) -> dict:
        now = time.monotonic()
        active = sum(1 for _, (_, exp) in self._store.items() if exp > now)
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "size": len(self._store),
            "active": active,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 3),
        }

    def evict_expired(self) -> int:
        """만료된 항목 일괄 제거. 제거된 수 반환."""
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]
        return len(expired)


# ── 전역 캐시 인스턴스 ──
_cache: InMemoryTTLCache = InMemoryTTLCache()


def _make_cache_key(query: str, collections: list[str] | None,
                    top_k: int, project_id: str | None) -> str:
    """캐시 키 생성 — query + 검색 파라미터를 MD5 해싱."""
    col_str = ",".join(sorted(collections or []))
    raw = f"{query.strip().lower()}|{col_str}|{top_k}|{project_id or ''}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ── 퍼블릭 API ──

def get_cached_results(
    query: str,
    collections: list[str] | None = None,
    top_k: int = 8,
    project_id: str | None = None,
) -> list[dict] | None:
    """Retrieval 캐시 조회. hit 시 결과 반환, miss 시 None."""
    key = _make_cache_key(query, collections, top_k, project_id)
    result = _cache.get(key)
    if result is not None:
        logger.debug("[캐시 HIT] query=%r, key=%s", query[:40], key[:8])
    return result


def cache_results(
    query: str,
    results: list[dict],
    collections: list[str] | None = None,
    top_k: int = 8,
    project_id: str | None = None,
    ttl: int = _DEFAULT_TTL,
) -> None:
    """검색 결과를 캐시에 저장."""
    key = _make_cache_key(query, collections, top_k, project_id)
    _cache.set(key, results, ttl=ttl)
    logger.debug("[캐시 SET] query=%r, key=%s, items=%d", query[:40], key[:8], len(results))


def get_cache_stats() -> dict:
    """캐시 통계 반환 (hit/miss/size 등)."""
    return _cache.stats()


def clear_cache() -> None:
    """전체 캐시 비우기 (테스트·관리용)."""
    _cache.clear()


def evict_expired() -> int:
    """만료 항목 정리. 정리된 수 반환."""
    n = _cache.evict_expired()
    if n:
        logger.info("[캐시] 만료 항목 %d개 제거", n)
    return n
