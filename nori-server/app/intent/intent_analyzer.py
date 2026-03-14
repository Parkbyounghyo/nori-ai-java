"""
코드 검색 의도 분류기 — 규칙 기반 (LLM 미사용)

사용자 질문을 검색 전략 타입으로 빠르게 분류한다.

── 구조 기반 intent (Structure Index DB 사용) ──
  struct_query    — 프로젝트 전체 흐름/구조 질문
  feature_location — 특정 기능 위치 질문 (어디서 처리해?)
  flow_trace      — 호출 흐름/처리 순서 추적 (Controller→Service→Mapper)
  data_flow       — 데이터 흐름/값 전달 경로 (테이블, SQL, API)
  page_connection — 화면 연결 질문 (JSP→JS→API)
  impact_analysis — 영향도 분석 (수정 시 영향 범위)
  reverse_trace   — 역방향 추적 (테이블 기준 화면/API 찾기)

── 기존 intent (임베딩/LLM 사용) ──
  feature_impl    — 기능 추가/구현
  error_analysis  — 오류/예외 분석
  config_search   — 설정 파일 검색
  doc_explain     — 개념/기술 설명
  code_search     — 코드 위치/파일 탐색 (기본값)
"""
import re

# ── 구조 기반 intent 상수 ──
STRUCT_QUERY = "struct_query"
FEATURE_LOCATION = "feature_location"
FLOW_TRACE = "flow_trace"
DATA_FLOW = "data_flow"
PAGE_CONNECTION = "page_connection"
IMPACT_ANALYSIS = "impact_analysis"
REVERSE_TRACE = "reverse_trace"

# ── 기존 intent 상수 ──
CODE_SEARCH = "code_search"
FEATURE_IMPL = "feature_impl"
ERROR_ANALYSIS = "error_analysis"
CONFIG_SEARCH = "config_search"
TEMPLATE_SEARCH = "template_search"
DOC_EXPLAIN = "doc_explain"

# intent → 사용 엔진 분류
STRUCTURE_INTENTS = {
    STRUCT_QUERY, FEATURE_LOCATION, FLOW_TRACE,
    DATA_FLOW, PAGE_CONNECTION, IMPACT_ANALYSIS, REVERSE_TRACE,
}
EMBEDDING_INTENTS = {
    CODE_SEARCH, FEATURE_IMPL, ERROR_ANALYSIS,
    CONFIG_SEARCH, TEMPLATE_SEARCH, DOC_EXPLAIN,
}
ALL_INTENTS = STRUCTURE_INTENTS | EMBEDDING_INTENTS


def is_structure_intent(intent: str) -> bool:
    """Structure Index DB 검색이 필요한 intent인지 판별"""
    return intent in STRUCTURE_INTENTS


# ── 패턴 (우선순위 높은 순) ──
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # ── 구조 기반 ──
    (ERROR_ANALYSIS, re.compile(
        r"(error|exception|오류|에러|문제가\s*있|실패|안\s*돼|안\s*되|왜\s*.{0,8}나|"
        r"stack.*trace|stacktrace|NPE|NullPointer|500|배포\s*오류)",
        re.I,
    )),
    (REVERSE_TRACE, re.compile(
        r"(어느\s*.{0,10}(?:에서|에)\s*(?:저장|수정|삭제|등록)|"
        r"(?:테이블|table).{0,15}(?:화면|페이지|api|url|controller)|"
        r"(?:TB_|table\s+)\w+.{0,20}(?:쓰는|사용하는|건드리는|조회하는|어디서|저장해|저장돼|수정해|삭제해)|"
        r"TB_[A-Z_]+.{0,10}(?:어디|어느|역추적)|"
        r"역추적|역방향|반대로.{0,10}찾|where.{0,10}table)",
        re.I,
    )),
    (IMPACT_ANALYSIS, re.compile(
        r"(영향\s*(?:범위|도|분석)|수정\s*(?:하면|했을때|시).{0,10}영향|"
        r"어디가\s*바뀌|어디.{0,6}영향|impact|side\s*effect|"
        r"바꾸면\s*(?:어디|뭐가)|연쇄\s*(?:영향|변경))",
        re.I,
    )),
    (PAGE_CONNECTION, re.compile(
        r"((?:jsp|html|화면).{0,15}(?:api|url|controller|백엔드|서버)|"
        r"(?:api|url|controller).{0,15}(?:jsp|html|화면|페이지)|"
        r"어느\s*(?:페이지|화면|jsp).{0,10}(?:연결|매핑|호출)|"
        r"버튼.{0,20}(?:어디|api|url)|클릭.{0,20}(?:어디로|서버))",
        re.I,
    )),
    (DATA_FLOW, re.compile(
        r"(어느\s*테이블|어떤\s*테이블|테이블\s*에\s*저장|db\s*에\s*저장|"
        r"어디에\s*저장|sql\s*(?:어디|어느)|mapper.{0,10}어디|"
        r"데이터\s*(?:흐름|전달|경로)|값.{0,10}(?:어디로|전달|저장))",
        re.I,
    )),
    (FLOW_TRACE, re.compile(
        r"((?:처리|실행)\s*흐름|플로우|call\s*(?:flow|graph)|"
        r"controller.{0,15}service|service.{0,15}mapper|"
        r"호출\s*(?:순서|흐름|관계)|어디서\s*호출|거쳐서|타고\s*가|"
        r"흐름\s*(?:알려줘|보여줘|추적)|어떻게\s*처리)",
        re.I,
    )),
    (FEATURE_LOCATION, re.compile(
        r"((?:기능|로직|처리).{0,10}(?:어디|어느\s*(?:파일|클래스|메서드))|"
        r"어디서\s*.{0,10}(?:기능|처리|구현)|"
        r"(?:로그인|등록|수정|삭제|조회|검색|인증|권한).{0,10}(?:어디|클래스|파일)|"
        r"어느\s*(?:클래스|파일|메서드).{0,10}(?:담당|처리|구현))",
        re.I,
    )),
    (STRUCT_QUERY, re.compile(
        r"(전체\s*(?:구조|흐름|아키텍처)|프로젝트\s*(?:구조|흐름)|"
        r"시스템\s*(?:구조|아키텍처|흐름)|레이어\s*구조|"
        r"(?:mvc|레이어).{0,10}구조|어떻게\s*구성|아키텍처)",
        re.I,
    )),
    # ── 기존 ──
    (CONFIG_SEARCH, re.compile(
        r"(설정\s*파일|config|properties|yml|yaml|application\.properties|"
        r"환경\s*변수|environment|빈\s*등록|@Bean|@Configuration)",
        re.I,
    )),
    (TEMPLATE_SEARCH, re.compile(
        r"(jsp|jspx|화면\s*(?:구성|개발|수정)|template|템플릿|레이아웃|"
        r"include\s*파일|프런트|front.?end|타일|tiles)",
        re.I,
    )),
    (FEATURE_IMPL, re.compile(
        r"(추가\s*(?:해줘|하려면|하고싶|해야)|구현\s*(?:해줘|하려면)|"
        r"만들어\s*줘|새로\s*.{0,6}(?:만들|생성|추가)|개발\s*해줘|작성\s*해줘|"
        r"기능\s*추가|CRUD|scaffold)",
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
    """질문 → intent 타입 반환 (규칙 기반, LLM 미사용)."""
    for intent, pattern in _PATTERNS:
        if pattern.search(question):
            return intent
    return CODE_SEARCH


def analyze_intent_detail(question: str) -> dict:
    """intent + 매칭 근거 + 엔진 종류 반환 (디버깅/로깅용)."""
    for intent, pattern in _PATTERNS:
        m = pattern.search(question)
        if m:
            return {
                "intent": intent,
                "matched": m.group(0),
                "engine": "structure" if is_structure_intent(intent) else "embedding",
            }
    return {
        "intent": CODE_SEARCH,
        "matched": None,
        "engine": "embedding",
    }
