"""
컨텍스트 빌더 — 검색 결과를 Intent에 따라 재구성

Intent별 정렬 전략:
  flow_trace      → Controller → Service → Mapper → JSP 레이어 순
  feature_impl    → 수정 대상 파일 → 관련 Service → Mapper → JSP
  error_analysis  → errors/community 우선, 나머지 점수순
  기타            → 점수 내림차순

최종 출력: LLM 프롬프트에 넣을 텍스트 또는 구조화된 dict
"""
from app.intent.intent_analyzer import FLOW_TRACE, FEATURE_IMPL, ERROR_ANALYSIS

# ── 파일 경로/타입 기반 레이어 순서 ──
# (매칭 문자열, 레이어 우선순위) — 낮을수록 먼저
_LAYER_RULES: list[tuple[str, int]] = [
    ("controller",   0),
    ("action",       0),
    ("service",      1),
    ("serviceimpl",  1),
    ("mapper",       2),
    ("dao",          2),
    (".xml",         3),
    ("mybatis",      3),
    (".jsp",         4),
    (".html",        4),
    (".js",          5),
    ("errors",       6),
    ("community",    7),
]


def _layer_rank(result: dict) -> int:
    """결과 한 건의 레이어 순위 반환 (낮을수록 Controller에 가까움)."""
    meta = result.get("metadata", {})
    fp = (meta.get("file_path") or meta.get("file") or "").lower().replace("\\", "/")
    col = (result.get("collection") or "").lower()
    typ = (meta.get("type") or "").lower()
    combined = " ".join([fp, col, typ])
    for keyword, rank in _LAYER_RULES:
        if keyword in combined:
            return rank
    return 99


def build_context(
    results: list[dict],
    intent: str,
    max_chars: int = 16000,
) -> str:
    """검색 결과를 intent에 따라 정렬 후 LLM용 컨텍스트 문자열로 반환.

    Args:
        results: EmbeddingService.search() 반환 목록
        intent: intent_analyzer.analyze_intent() 반환값
        max_chars: 최대 컨텍스트 문자 수

    Returns:
        "--- 구분선 ---" 으로 이어진 텍스트 블록
    """
    if not results:
        return ""

    sorted_results = _sort_results(results, intent)

    parts: list[str] = []
    total = 0
    for r in sorted_results:
        meta = r.get("metadata", {})
        col = r.get("collection", "")
        fp = (meta.get("file_path") or meta.get("file") or "").replace("\\", "/")
        name = (
            meta.get("name")
            or meta.get("method_name")
            or meta.get("sql_id")
            or ""
        )
        text = r.get("text", "")
        score = r.get("score", 0.0)

        header = f"[{col}]"
        if fp:
            header += f" {fp}"
        if name:
            header += f" — {name}"
        header += f" (score:{score:.3f})"

        chunk = f"{header}\n{text}"
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(chunk[:remaining])
            break
        parts.append(chunk)
        total += len(chunk) + 1

    return "\n\n---\n\n".join(parts)


def build_context_structured(results: list[dict], intent: str) -> dict:
    """구조화된 컨텍스트 반환 (API 응답 / 디버깅용).

    Returns:
        {
            "intent": str,
            "layers": { collection_name: [{ text, score, metadata, layer_rank }] },
            "total": int,
        }
    """
    sorted_results = _sort_results(results, intent)
    layers: dict[str, list[dict]] = {}
    for r in sorted_results:
        col = r.get("collection", "unknown")
        layers.setdefault(col, []).append({
            "text": r.get("text", "")[:500],
            "score": round(r.get("score", 0.0), 4),
            "metadata": r.get("metadata", {}),
            "layer_rank": _layer_rank(r),
        })
    return {
        "intent": intent,
        "layers": layers,
        "total": sum(len(v) for v in layers.values()),
    }


# ── 내부 정렬 로직 ──

def _sort_results(results: list[dict], intent: str) -> list[dict]:
    if intent in (FLOW_TRACE, FEATURE_IMPL):
        # 레이어 순서 우선, 같은 레이어 내에서는 점수 내림차순
        return sorted(results, key=lambda r: (_layer_rank(r), -r.get("score", 0.0)))
    if intent == ERROR_ANALYSIS:
        def _error_key(r: dict) -> tuple:
            col = r.get("collection", "")
            priority = 0 if col in ("errors", "community") else 1
            return (priority, -r.get("score", 0.0))
        return sorted(results, key=_error_key)
    # 기본: 점수 내림차순
    return sorted(results, key=lambda r: -r.get("score", 0.0))
