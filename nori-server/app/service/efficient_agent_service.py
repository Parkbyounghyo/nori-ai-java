"""
효율적 에이전트 프로세스 — LLM 호출 최소화

1. 의도 분석 & 키워드 추출: 규칙 기반 (LLM 없음)
2. 파일 검색: 벡터 DB / 검색엔진 (LLM 없음)
3. 컨텍스트 조립: 검색 결과 요약 (LLM 없음)
4. 최종 추론: LLM 1회만 사용
"""
import logging
from typing import AsyncGenerator

from app.service.keyword_extractor import extract_keywords, keywords_to_query

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
    """키워드 기반 벡터 검색 (LLM 미사용)

    프로젝트 있으면 profiles 컬렉션 우선, 없으면 전체 검색.
    """
    keywords = extract_keywords(question)
    query = keywords_to_query(keywords) or question
    if not query.strip():
        return []

    all_results = []

    # 프로젝트 프로필 검색
    if project_id and user_id and hasattr(emb, "search_profiles"):
        try:
            profile_results = await emb.search_profiles(
                query=query, user_id=user_id, project_id=project_id, top_k=top_k
            )
            all_results.extend(profile_results)
            logger.info("[효율에이전트] profiles 검색 %d건", len(profile_results))
        except Exception as e:
            logger.warning("[효율에이전트] profiles 검색 실패: %s", e)

    # 공통 컬렉션 검색 (javadoc, spring, egov, errors 등)
    if emb.is_available:
        try:
            common_results = await emb.search(query=query, top_k=top_k)
            for r in common_results:
                if r not in all_results:
                    all_results.append(r)
            logger.info("[효율에이전트] 공통 컬렉션 검색 %d건", len(common_results))
        except Exception as e:
            logger.warning("[효율에이전트] 공통 검색 실패: %s", e)

    # 점수 기준 정렬 후 상위만 반환
    all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return all_results[:top_k]


def assemble_context(results: list[dict], max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """검색 결과를 하나의 컨텍스트로 조립 (함수명·문법 정보 위주)

    벡터 검색 결과의 text는 이미 프로필/소스구조 요약이므로 그대로 사용.
    """
    if not results:
        return ""

    parts = []
    total = 0
    seen = set()
    for r in results:
        text = r.get("text", "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        meta = r.get("metadata", {})
        file_path = meta.get("file_path", meta.get("path", ""))
        prefix = f"[{file_path}]\n" if file_path else ""
        block = f"{prefix}{text}\n\n"
        if total + len(block) > max_chars:
            remain = max_chars - total - 100
            if remain > 100:
                block = block[:remain] + "\n...(생략)\n\n"
            parts.append(block)
            total += len(block)
            break
        parts.append(block)
        total += len(block)

    return "".join(parts).strip()


async def efficient_agent_complete(
    llm,
    question: str,
    assembled_context: str,
    history: list[dict] | None = None,
) -> str:
    """최종 추론 — LLM 1회만 호출

    '여기 네가 고쳐야 할 소스 코드 조각들이야. 이걸 보고 에러를 해결해줘.'
    """
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
        messages.extend(history[-4:])
    messages.append({"role": "user", "content": user_content})

    result = await llm.complete_messages(messages)
    return result or ""


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
        messages.extend(history[-4:])
    messages.append({"role": "user", "content": user_content})

    async for token in llm.stream_messages(messages):
        yield token
