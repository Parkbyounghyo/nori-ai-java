"""
효율적 에이전트 프로세스 — LLM 호출 최소화

파이프라인:
  질문 → Intent Analyzer → Search Planner → Query Expansion
       → Vector Search (multi-collection) → Rerank → Context Builder → LLM 1회
"""
import logging
from typing import AsyncGenerator

from app.service.keyword_extractor import extract_keywords, keywords_to_query
from app.intent.intent_analyzer import analyze_intent
from app.search.search_planner import plan_search
from app.search.query_expander import expand_query
from app.search.retrieval_cache import get_cached_results, cache_results
from app.context.context_builder import build_context

logger = logging.getLogger("nori-server")

# 컨텍스트 최대 길이
MAX_CONTEXT_CHARS = 16000


async def search_code_with_keywords(
    emb,
    question: str,
    user_id: str = "default",
    project_id: str | None = None,
    top_k: int = 12,
) -> list[dict]:
    """Intent → Search Planner → Query Expansion → 벡터 검색 파이프라인 (LLM 미사용).

    1. 의도 분류 → 컬렉션·top_k 결정
    2. 쿼리 확장 → 최대 3개 쿼리
    3. 확장 쿼리별 지정 컬렉션 검색
    4. 점수 정렬 → 상위 top_k 반환
    """
    # ── 1. Intent 분류 & 검색 계획 ──
    intent = analyze_intent(question)
    plan = plan_search(intent, top_k_override=top_k, project_id=project_id)
    logger.info("[파이프라인] intent=%s, collections=%s, top_k=%d",
                intent, plan.collections, plan.top_k)

    # ── 2. 쿼리 확장 ──
    base_query = keywords_to_query(extract_keywords(question)) or question
    expanded_queries = expand_query(base_query, max_expansions=plan.query_expansion_count)
    logger.info("[파이프라인] 확장 쿼리 %d개: %s", len(expanded_queries), expanded_queries[:2])

    # ── 2-a. Retrieval Cache 조회 ──
    cached = get_cached_results(base_query, plan.collections, plan.top_k, project_id)
    if cached is not None:
        logger.info("[파이프라인] 캐시 HIT — %d건 반환", len(cached))
        return cached

    all_results: list[dict] = []
    seen_ids: set[str] = set()

    if not emb.is_available:
        return []

    # ── 3. 확장 쿼리별 지정 컬렉션 검색 ──
    for q in expanded_queries:
        try:
            project_filter = {"project": project_id} if project_id else None
            results = await emb.search(
                query=q,
                top_k=plan.top_k,
                collections=plan.collections,
                filters=project_filter,
            )
            for r in results:
                rid = r.get("id") or r.get("text", "")[:60]
                if rid not in seen_ids:
                    all_results.append(r)
                    seen_ids.add(rid)
        except Exception as e:
            logger.warning("[파이프라인] 검색 오류 (query=%r): %s", q[:40], e)

    # ── 4. 프로파일 검색 (보조) ──
    if project_id and user_id and hasattr(emb, "search_profiles"):
        try:
            profile_results = await emb.search_profiles(
                query=base_query, user_id=user_id, project_id=project_id, top_k=6
            )
            for r in profile_results:
                rid = r.get("id") or r.get("text", "")[:60]
                if rid not in seen_ids:
                    all_results.append(r)
                    seen_ids.add(rid)
            logger.info("[파이프라인] profiles 검색 %d건", len(profile_results))
        except Exception as e:
            logger.warning("[파이프라인] profiles 검색 실패: %s", e)

    # ── 5. 점수 정렬 → 상위 top_k ──
    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    final = all_results[:plan.top_k]
    logger.info("[파이프라인] 최종 검색 결과 %d건 (intent=%s)", len(final), intent)

    # ── 6. 캐시 저장 ──
    if final:
        cache_results(base_query, final, plan.collections, plan.top_k, project_id)

    return final


def assemble_context(
    results: list[dict],
    intent: str = "code_search",
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """검색 결과를 Intent에 맞춰 정렬 후 LLM 컨텍스트 문자열로 조립.

    Intent 분류 결과를 반영하여 flow_trace 시 레이어 순서,
    error_analysis 시 errors/community 우선 정렬.
    """
    return build_context(results, intent=intent, max_chars=max_chars)


async def efficient_agent_complete(
    llm,
    question: str,
    assembled_context: str,
    history: list[dict] | None = None,
) -> str:
    """최종 추론 — LLM 1회만 호출 + Safety Guard 적용

    LLM 응답에서 코드 블록을 추출하여 Safety Guard 검사 후
    위반이 있으면 경고 메시지를 응답에 덧붙인다.
    """
    from app.service.safety_guard import check_code_safety
    system = (
        "당신은 같은 팀 시니어 Java 개발자입니다.\n"
        "사용자가 제공한 참고 코드 조각들을 보고 요청대로 수정/해결 방법을 제시합니다.\n\n"
        "규칙:\n"
        "1. 제공된 코드 조각만을 기반으로 답변하세요. 없는 클래스·메서드를 만들어내지 마세요.\n"
        "2. 수정이 필요하면 변경할 부분만 구체적으로 보여주세요.\n"
        "3. 설정 파일(application.properties 등) 수정이 필요한 경우 해당 항목과 값을 명시하세요.\n"
        "4. 기술 스택: Java, Spring, eGovFrame, MyBatis\n"
        "\n모든 답변은 한국어로 작성하세요."
    )

    user_content = (
        f"요청: {question}\n\n"
        "아래는 프로젝트에서 검색된 관련 코드·설명 조각입니다. "
        "이를 참고하여 위 요청대로 수정 코드나 해결 방법을 작성해주세요.\n\n"
        f"{assembled_context[:MAX_CONTEXT_CHARS]}"
    )

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})

    result = await llm.complete_messages(messages)
    answer = result or ""

    # Safety Guard — 응답 코드 블록 검사
    code_blocks = _extract_code_blocks(answer)
    for block in code_blocks:
        guard = check_code_safety(block)
        if not guard.safe:
            logger.warning("[SafetyGuard] LLM 응답 코드에서 위험 패턴 감지: %s", guard.summary())
            answer += (
                f"\n\n> ⚠️ **Safety Guard 경고**: 위 코드에서 주의가 필요한 패턴이 감지되었습니다.\n"
                + "\n".join(f"> - {v['message']}" for v in guard.violations)
            )
            break

    return answer


async def efficient_agent_stream(
    llm,
    question: str,
    assembled_context: str,
    history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """최종 추론 스트리밍 — LLM 1회만 호출"""
    system = (
        "당신은 같은 팀 시니어 Java 개발자입니다.\n"
        "사용자가 제공한 참고 코드 조각들을 보고 요청대로 수정/해결 방법을 제시합니다.\n\n"
        "규칙:\n"
        "1. 제공된 코드 조각만을 기반으로 답변하세요. 없는 클래스·메서드를 만들어내지 마세요.\n"
        "2. 수정이 필요하면 변경할 부분만 구체적으로 보여주세요.\n"
        "3. 설정 파일(application.properties 등) 수정이 필요한 경우 해당 항목과 값을 명시하세요.\n"
        "4. 기술 스택: Java, Spring, eGovFrame, MyBatis\n"
        "\n모든 답변은 한국어로 작성하세요."
    )

    user_content = (
        f"요청: {question}\n\n"
        "아래는 프로젝트에서 검색된 관련 코드·설명 조각입니다. "
        "이를 참고하여 위 요청대로 수정 코드나 해결 방법을 작성해주세요.\n\n"
        f"{assembled_context[:MAX_CONTEXT_CHARS]}"
    )

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})

    async for token in llm.stream_messages(messages):
        yield token


# ── 내부 헬퍼 ──

def _extract_code_blocks(text: str) -> list[str]:
    """마크다운 코드 블록(``` ... ```) 추출."""
    import re
    return re.findall(r"```(?:\w+)?\n(.*?)```", text, re.S)
