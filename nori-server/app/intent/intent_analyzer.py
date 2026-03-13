"""
코드 검색 의도 분류기 — 규칙 기반 (LLM 미사용)

사용자 질문을 검색 전략 타입으로 빠르게 분류한다.

intent 타입:
  code_search     — 코드 위치/파일 탐색
  flow_trace      — 호출 흐름/처리 순서 추적
  feature_impl    — 기능 추가/구현
  error_analysis  — 오류/예외 분석
  config_search   — 설정 파일 검색
  template_search — UI 템플릿/JSP 검색
  doc_explain     — 개념/기술 설명
"""
import re

# ── intent 상수 ──
CODE_SEARCH = "code_search"
FLOW_TRACE = "flow_trace"
FEATURE_IMPL = "feature_impl"
ERROR_ANALYSIS = "error_analysis"
CONFIG_SEARCH = "config_search"
TEMPLATE_SEARCH = "template_search"
DOC_EXPLAIN = "doc_explain"

ALL_INTENTS = {
    CODE_SEARCH, FLOW_TRACE, FEATURE_IMPL, ERROR_ANALYSIS,
    CONFIG_SEARCH, TEMPLATE_SEARCH, DOC_EXPLAIN,
}

# ── 패턴 (우선순위 높은 순) ──
_PATTERNS: list[tuple[str, re.Pattern]] = [
    (ERROR_ANALYSIS, re.compile(
        r"(error|exception|오류|에러|문제가\s*있|실패|안\s*돼|안\s*되|왜\s*.{0,8}나|"
        r"stack.*trace|stacktrace|NPE|NullPointer|500|배포\s*오류)",
        re.I,
    )),
    (CONFIG_SEARCH, re.compile(
        r"(설정\s*파일|config|properties|yml|yaml|application\.properties|"
        r"환경\s*변수|environment|빈\s*등록|@Bean|@Configuration)",
        re.I,
    )),
    (TEMPLATE_SEARCH, re.compile(
        r"(jsp|jspx|화면\s*(?:구성|개발|수정)|UI|template|템플릿|레이아웃|"
        r"include\s*파일|프런트|front.?end|타일|tiles)",
        re.I,
    )),
    (FEATURE_IMPL, re.compile(
        r"(추가\s*(?:해줘|하려면|하고싶|해야)|구현\s*(?:해줘|하려면)|"
        r"만들어\s*줘|새로\s*.{0,6}(?:만들|생성|추가)|개발\s*해줘|작성\s*해줘|"
        r"기능\s*추가|CRUD|scaffold)",
        re.I,
    )),
    (FLOW_TRACE, re.compile(
        r"(흐름|처리\s*흐름|플로우|flow|어떻게\s*.{0,8}처리|"
        r"호출\s*.{0,8}순서|어디서\s*.{0,8}호출|거쳐서|타고\s*가|"
        r"어느\s*파일.{0,6}처리|어떤\s*클래스)",
        re.I,
    )),
    (DOC_EXPLAIN, re.compile(
        r"(뭐야|뭐지|무엇인가|what\s*is|설명\s*(?:해줘|해|좀)|이란\s*(?:무엇|뭐)|"
        r"개념\s*(?:이|이야)|어떤\s*.{0,8}(?:거야|건가|건지)|"
        r"왜\s*.{0,6}쓰|언제\s*.{0,6}쓰)",
        re.I,
    )),
]


def analyze_intent(question: str) -> str:
    """질문 → 검색 intent 타입 반환 (규칙 기반, LLM 미사용).

    매칭 우선순위: error > config > template > feature > flow > doc > code_search
    """
    for intent, pattern in _PATTERNS:
        if pattern.search(question):
            return intent
    return CODE_SEARCH


def analyze_intent_detail(question: str) -> dict:
    """intent + 매칭 근거 반환 (디버깅/로깅용)."""
    for intent, pattern in _PATTERNS:
        m = pattern.search(question)
        if m:
            return {"intent": intent, "matched": m.group(0)}
    return {"intent": CODE_SEARCH, "matched": None}
