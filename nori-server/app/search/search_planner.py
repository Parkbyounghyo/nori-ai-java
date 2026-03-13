"""
검색 플래너 — Intent에 따라 컬렉션 전략과 검색 파라미터를 결정한다.

사용 예:
    plan = plan_search("flow_trace")
    # plan.collections == ["custom", "javadoc"]
    # plan.top_k == 8
    # plan.use_symbol_graph == True
"""
from dataclasses import dataclass, field

from app.intent.intent_analyzer import (
    CODE_SEARCH,
    FLOW_TRACE,
    FEATURE_IMPL,
    ERROR_ANALYSIS,
    CONFIG_SEARCH,
    TEMPLATE_SEARCH,
    DOC_EXPLAIN,
)

# ── intent → 컬렉션 목록 (우선순위 순) ──
_COLLECTION_MAP: dict[str, list[str]] = {
    FLOW_TRACE:      ["custom", "javadoc"],
    ERROR_ANALYSIS:  ["errors", "community", "custom", "egov"],
    FEATURE_IMPL:    ["custom", "spring", "egov", "javadoc"],
    CONFIG_SEARCH:   ["spring", "egov", "community"],
    TEMPLATE_SEARCH: ["project-templates", "web-ui", "desktop-ui"],
    DOC_EXPLAIN:     ["javadoc", "spring", "egov", "community"],
    CODE_SEARCH:     ["custom", "javadoc", "spring", "egov"],
}

# ── intent → 기본 top_k ──
_TOP_K_MAP: dict[str, int] = {
    FLOW_TRACE:      8,
    ERROR_ANALYSIS:  8,
    FEATURE_IMPL:    10,
    CONFIG_SEARCH:   5,
    TEMPLATE_SEARCH: 5,
    DOC_EXPLAIN:     6,
    CODE_SEARCH:     8,
}

# ── intent → 쿼리 확장 수 ──
_EXPANSION_MAP: dict[str, int] = {
    FLOW_TRACE:      2,
    ERROR_ANALYSIS:  2,
    FEATURE_IMPL:    3,
    CONFIG_SEARCH:   1,
    TEMPLATE_SEARCH: 1,
    DOC_EXPLAIN:     2,
    CODE_SEARCH:     2,
}

# Symbol graph가 유용한 intent
_SYMBOL_GRAPH_INTENTS = {FLOW_TRACE, FEATURE_IMPL}


@dataclass
class SearchPlan:
    """검색 실행 계획"""
    intent: str
    collections: list[str] = field(default_factory=list)
    top_k: int = 8
    query_expansion_count: int = 2
    use_symbol_graph: bool = False

    def __repr__(self) -> str:
        return (
            f"SearchPlan(intent={self.intent!r}, "
            f"collections={self.collections}, "
            f"top_k={self.top_k}, "
            f"expansion={self.query_expansion_count}, "
            f"symbol_graph={self.use_symbol_graph})"
        )


def plan_search(
    intent: str,
    top_k_override: int | None = None,
    project_id: str | None = None,
) -> SearchPlan:
    """intent → SearchPlan 반환.

    Args:
        intent: analyze_intent() 반환값
        top_k_override: top_k 직접 지정 (None이면 intent 기본값 사용)
        project_id: 프로젝트 ID (있으면 custom 컬렉션 필터 활성화 힌트)
    """
    collections = list(_COLLECTION_MAP.get(intent, _COLLECTION_MAP[CODE_SEARCH]))
    # 프로젝트 있으면 custom/project-templates 우선 배치
    if project_id and "custom" in collections and collections[0] != "custom":
        collections.insert(0, "custom")
        # 중복 제거 순서 유지
        seen: set[str] = set()
        deduped: list[str] = []
        for c in collections:
            if c not in seen:
                deduped.append(c)
                seen.add(c)
        collections = deduped

    return SearchPlan(
        intent=intent,
        collections=collections,
        top_k=top_k_override or _TOP_K_MAP.get(intent, 8),
        query_expansion_count=_EXPANSION_MAP.get(intent, 2),
        use_symbol_graph=intent in _SYMBOL_GRAPH_INTENTS,
    )
