"""
검색/대화 API — 문서 검색, 정밀 검색, 자유 대화, 임베딩 관리
"""
import json
import logging
import re
import time
from datetime import datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.api.deps import LlmDep, EmbeddingDep, verify_api_key, get_llm_service, get_embedding_service
from app.api.models import (
    SearchRequest, ChatRequest, EmbeddingAddRequest,
    ProfileSummarizeRequest, DescribeFileRequest,
    PickFilesRequest, SmartChatRequest, NoriResponse,
)

logger = logging.getLogger("nori-server")


def _log_step(t_start: float, step: str, extra: str = ""):
    """단계별 시간 로그 — 터미널 + logger 출력"""
    elapsed = time.time() - t_start
    ts = datetime.now().strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
    msg = f"[{ts}] +{elapsed:.1f}s [{step}] {extra}".strip()
    logger.info("NORI_LOG %s", msg)
    print(msg, flush=True)  # 터미널에 바로 표시


router = APIRouter(
    prefix="/api/v1",
    tags=["search-chat"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("/search", response_model=NoriResponse)
async def search_docs(req: SearchRequest, emb: EmbeddingDep):
    """문서 검색 — 벡터DB에서 유사 문서 검색"""
    filters = {}
    if req.java_version:
        filters["java_version"] = req.java_version

    results = await emb.search(
        query=req.query,
        top_k=req.top_k,
        collections=req.collections,
        filters=filters if filters else None,
    )

    formatted = []
    for r in results:
        formatted.append({
            "text": r["text"][:1000],
            "score": round(r["score"], 4),
            "collection": r["collection"],
            "metadata": r["metadata"],
        })

    return NoriResponse(
        data={"results": formatted, "total": len(formatted)},
        search_depth="chroma",
    )


@router.post("/search/deep", response_model=NoriResponse)
async def deep_search(req: SearchRequest, emb: EmbeddingDep):
    """정밀 검색 — 모든 컬렉션에서 확장 검색"""
    results = await emb.search(
        query=req.query,
        top_k=max(req.top_k, 10),
    )

    formatted = []
    for r in results:
        formatted.append({
            "text": r["text"][:1500],
            "score": round(r["score"], 4),
            "collection": r["collection"],
            "metadata": r["metadata"],
        })

    return NoriResponse(
        data={"results": formatted, "total": len(formatted)},
        search_depth="chroma",  # Milvus 활성화 시 "both"로 변경
    )


# ── 효율적 에이전트 (LLM 1회만 사용) ──
@router.post("/chat/efficient", response_model=NoriResponse)
async def chat_efficient(
    req: SmartChatRequest,
    llm: LlmDep,
    emb: EmbeddingDep,
):
    """효율적 에이전트: 키워드 추출(규칙) → 벡터 검색 → 컨텍스트 조립 → LLM 1회 호출"""
    from app.service import chat_session_service as css
    _raw_history = css.build_history_from_request(
        req.user_id or "default", req.session_id or "",
        req.history or [], req.selected_indices,
    )
    effective_history = _trim_history_if_context_shifted(req.message, _raw_history, req.profile)
    resolved_msg, pr_meta = _resolve_pronouns_in_message(req.message, effective_history)
    effective_message = resolved_msg if (pr_meta.get('replaced') or pr_meta.get('subject_inferred')) else req.message
    from app.service.efficient_agent_service import (
        search_code_with_keywords,
        assemble_context,
        efficient_agent_complete,
    )
    user_id = req.user_id or "default"
    project_id = (req.project_id or "").strip() or None

    results = await search_code_with_keywords(
        emb, effective_message, user_id=user_id, project_id=project_id, top_k=12
    )
    ctx = assemble_context(results)
    if not ctx:
        logger.warning("[효율에이전트] 검색 결과 없음 — LLM만 호출")
    answer = await efficient_agent_complete(
        llm, effective_message, ctx, history=effective_history
    )
    return NoriResponse(data={
        "answer": answer,
        "search_results_count": len(results),
    })


@router.post("/chat/efficient/stream")
async def chat_efficient_stream(
    req: SmartChatRequest,
    llm: LlmDep,
    emb: EmbeddingDep,
):
    """효율적 에이전트 스트리밍 — 키워드 검색 → LLM 1회 스트리밍"""
    from app.service import chat_session_service as css
    _raw_history = css.build_history_from_request(
        req.user_id or "default", req.session_id or "",
        req.history or [], req.selected_indices,
    )
    effective_history = _trim_history_if_context_shifted(req.message, _raw_history, req.profile)
    resolved_msg, pr_meta = _resolve_pronouns_in_message(req.message, effective_history)
    effective_message = resolved_msg if (pr_meta.get('replaced') or pr_meta.get('subject_inferred')) else req.message
    from app.service.efficient_agent_service import (
        search_code_with_keywords,
        assemble_context,
        efficient_agent_stream,
    )

    user_id = req.user_id or "default"
    project_id = req.project_id or None

    results = await search_code_with_keywords(
        emb, effective_message, user_id=user_id, project_id=project_id, top_k=12
    )
    ctx = assemble_context(results)
    if not ctx:
        logger.warning("[효율에이전트 스트림] 검색 결과 없음")

    async def gen():
        yield _sse("status", {"message": f"검색 완료: {len(results)}개 조각", "step": "search_done"})
        async for token in efficient_agent_stream(llm, effective_message, ctx, history=effective_history):
            yield _sse("token", {"content": token})
        yield _sse("done", {})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/chat/pick-files", response_model=NoriResponse)
async def pick_files(req: PickFilesRequest, llm: LlmDep):
    """프로필을 보고 질문에 필요한 파일 경로 목록 반환"""
    files_csv = await llm.pick_files(req.question, req.profile)
    return NoriResponse(data={"files": files_csv})


@router.post("/chat/smart", response_model=NoriResponse)
async def smart_chat(req: SmartChatRequest, llm: LlmDep, emb: EmbeddingDep):
    """스마트 채팅 — 의도 분류 + 태스크 분할 + 라우팅 (use_efficient 시 LLM 1회만)"""
    from app.service import chat_session_service as css
    _raw_history = css.build_history_from_request(
        req.user_id or "default", req.session_id or "",
        req.history or [], req.selected_indices,
    )
    effective_history = _trim_history_if_context_shifted(req.message, _raw_history, req.profile)

    resolved_msg, pronoun_meta = _resolve_pronouns_in_message(req.message, effective_history)
    effective_message = resolved_msg if (pronoun_meta.get('replaced') or pronoun_meta.get('subject_inferred')) else req.message

    if req.use_efficient:
        from app.service.efficient_agent_service import (
            search_code_with_keywords,
            assemble_context,
            efficient_agent_complete,
        )
        user_id = req.user_id or "default"
        project_id = (req.project_id or "").strip() or None
        results = await search_code_with_keywords(
            emb, effective_message, user_id=user_id, project_id=project_id, top_k=12
        )
        ctx = assemble_context(results)
        answer = await efficient_agent_complete(llm, effective_message, ctx, history=effective_history)
        return NoriResponse(data={"answer": answer, "search_results_count": len(results)})

    profile = await _resolve_profile(req)

    # 1단계: ★ 고속 분류 — 키워드 기반 의도 분류 + 파일 선별 (LLM 호출 없음)
    has_code = bool(req.selected_code)
    fast_intent, fast_files = _fast_classify(effective_message, has_code, profile, req.message)
    fallback_search = False

    if fast_intent and (fast_files or fast_intent in ("ERROR_ANALYZE", "ERROR_FIX")):
        tasks = [{"intent": fast_intent, "detail": effective_message, "files": fast_files[:15]}]
        needed_files = fast_files[:15]
        logger.info("[고속분류] %s → %s, 파일 %d개", effective_message[:50], fast_intent, len(needed_files))
    else:
        if profile:
            route_json = await llm.classify_and_route(effective_message, profile, has_code)
            logger.info("[분류 결과] %s → %s", effective_message[:80], route_json[:200])
        else:
            route_json = '{"tasks":[{"intent":"QUESTION","detail":"' + effective_message.replace('"', '\\"') + '","files":[]}]}'

        tasks = _parse_tasks(route_json)
        if not tasks:
            tasks = [{"intent": "QUESTION", "detail": effective_message, "files": []}]
        tasks = _dedup_tasks(tasks)

        needed_files = []
        for t in tasks:
            for f in t.get("files", []):
                if f not in needed_files:
                    needed_files.append(f)

        if needed_files and profile:
            needed_files = _fix_file_paths(needed_files, profile, req.message)
            needed_files = _expand_related_files(needed_files, profile, question=req.message)
            for t in tasks:
                t["files"] = list(needed_files)[:15]

        if not needed_files and profile:
            needed_files = _keyword_search_paths(req.message, profile, max_results=15)
        if needed_files:
            needed_files = _expand_related_files(needed_files, profile, question=req.message)
            for t in tasks:
                t["files"] = needed_files[:15]
            fallback_search = True
            logger.info("[키워드 폴백] %d개 파일 발견", len(needed_files))

    # 3단계: 파일 내용이 제공되었으면 각 태스크 실행, 아니면 파일 목록만 반환
    if not req.file_contents:
        # 1차 응답: 파일 목록 + 태스크 계획 반환
        return NoriResponse(data={
            "phase": "need_files",
            "tasks": tasks,
            "needed_files": needed_files[:15],
            "fallback_search": fallback_search,
        })

    # 4단계: 파일 내용 제공됨 → 각 태스크 라우팅 실행
    rag_context = ""
    if req.use_rag and emb.is_available:
        results = await emb.search(query=req.message, top_k=5)
        if req.project_id and req.user_id:
            try:
                pr = await emb.search_profiles(req.message, req.user_id, req.project_id, top_k=5)
                if pr:
                    results = pr + results
                    results = results[:8]
            except Exception:
                pass
        if results:
            rag_context = "\n---\n".join(
                f"[{r.get('collection','')}] {r['text'][:600]}" for r in results
            )

    # 파일 내용을 하나의 컨텍스트로 합침
    file_ctx = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in req.file_contents.items()
    )

    results = []
    for t in tasks:
        intent = t.get("intent", "QUESTION")
        detail = t.get("detail", req.message)
        code = req.selected_code or file_ctx

        try:
            if intent == "EXPLAIN" and code:
                answer = await llm.explain_code(code, rag_context=rag_context)
            elif intent == "REVIEW" and code:
                answer = await llm.review_code(code, rag_context=rag_context)
            elif intent == "GENERATE":
                answer = await llm.generate_code(detail, project_context=file_ctx, rag_context=rag_context)
            elif intent == "REFACTOR" and code:
                answer = await llm.refactor_code(code, instruction=detail, rag_context=rag_context)
            elif intent == "ERROR_FIX" and code:
                answer = await llm.fix_error(detail, code, rag_context=rag_context)
            elif intent == "ERROR_ANALYZE":
                answer = await llm.analyze_error(detail, code=code, rag_context=rag_context)
            elif intent == "GENERATE_DOC" and code:
                answer = await llm.generate_javadoc(code, rag_context=rag_context)
            elif intent == "GENERATE_TEST" and code:
                answer = await llm.generate_test(code, rag_context=rag_context)
            elif intent == "SEARCH":
                answer = await llm.chat(
                    question=detail, history=effective_history,
                    rag_context=rag_context, project_context=file_ctx,
                )
            else:
                answer = await llm.chat(
                    question=detail, history=effective_history,
                    rag_context=rag_context, project_context=file_ctx,
                )
        except Exception as e:
            answer = f"❌ {intent} 실행 실패: {e}"

        results.append({"intent": intent, "detail": detail, "answer": answer})

    # 결과 합산
    if len(results) == 1:
        combined = results[0]["answer"]
    else:
        parts = []
        for i, r in enumerate(results):
            label = _intent_label(r["intent"])
            parts.append(f"## {label} {i+1}. {r['detail']}\n\n{r['answer']}")
        combined = "\n\n---\n\n".join(parts)

    return NoriResponse(
        data={"phase": "done", "answer": combined, "tasks": tasks},
        search_depth="chroma" if rag_context else "none",
    )


_pronoun_resolver = None

def _get_pronoun_resolver():
    global _pronoun_resolver
    if _pronoun_resolver is None:
        from app.service.pronoun_resolver import PronounResolver
        _pronoun_resolver = PronounResolver()
    return _pronoun_resolver


def _resolve_pronouns_in_message(message: str, history: list) -> tuple[str, dict]:
    """대명사/지시어 해석 — 현재 메시지를 history 기반으로 치환"""
    try:
        resolver = _get_pronoun_resolver()
        resolved, meta = resolver.resolve(message, history)
        return resolved, meta
    except Exception as e:
        logger.warning("[대명사] 해석 오류: %s", e)
        return message, {'replaced': False}


_topic_domain_cache: dict = {}  # profile hash -> {keyword: domain_name}

def _extract_topic_domains(text: str, profile: str = None) -> set[str]:
    """질문/대화에서 주제 도메인 추출 (맥락 전환 감지용)
    기본 도메인 + 프로필 기반 도메인 동적 추가"""
    if not text or not text.strip():
        return set()
    t = text.lower().strip()
    domains = set()

    # 기본 도메인 (프로젝트 무관한 기술 영역)
    _base_rules = [
        (("업로드", "다운로드", "첨부", "용량", "제한", "크기", "multipart", "attach"), "file_upload"),
        (("결제", "pay", "주문", "order", "카드", "pg", "환불", "refund"), "pay"),
        (("회원", "member", "로그인", "login", "auth", "인증", "비밀번호"), "member"),
        (("포인트", "point", "쿠폰", "coupon", "적립", "할인"), "point"),
        (("상품", "product", "장바구니", "cart", "카탈로그"), "product"),
        (("관리자", "admin", "어드민"), "admin"),
        (("배치", "batch", "스케줄", "schedule", "cron"), "batch"),
        (("보안", "security", "권한", "role", "xss", "csrf"), "security"),
        (("데이터베이스", "db", "쿼리", "query", "sql", "mybatis", "mapper", "테이블"), "db"),
    ]
    for keywords, domain_name in _base_rules:
        if any(w in t for w in keywords):
            domains.add(domain_name)

    # ★ 프로필 기반 도메인 — 프로젝트별 기능 블록에서 자동 추출
    if profile:
        global _topic_domain_cache
        p_hash = str(hash(profile[:200]))
        if p_hash not in _topic_domain_cache:
            dm = _build_domain_map(profile)
            kw_map = {}
            for block in dm:
                title = block.get("title", "").lower()
                for kw in block.get("keywords", []):
                    kw_map[kw.lower()] = title
            _topic_domain_cache[p_hash] = kw_map
        for kw, domain_name in _topic_domain_cache[p_hash].items():
            if kw in t:
                domains.add(domain_name)

    return domains


def _trim_history_if_context_shifted(current_message: str, history: list, profile: str = None) -> list:
    """맥락 전환 시 이전 대화 잘라냄 — 현재 질문과 이전 턴 주제가 다르면 history 비움"""
    if not history:
        return history
    current_domains = _extract_topic_domains(current_message, profile)
    if not current_domains:
        return history
    last_user_content = ""
    for m in reversed(history):
        if m.get("role") == "user":
            last_user_content = m.get("content", "") or ""
            break
    prev_domains = _extract_topic_domains(last_user_content, profile)
    if not prev_domains:
        return history
    overlap = current_domains & prev_domains
    if overlap:
        return history
    logger.info("[맥락전환] 현재=%s, 이전=%s → history 비움 (이전 주제 배제)",
                current_domains, prev_domains)
    return []


def _intent_label(intent: str) -> str:
    labels = {
        "EXPLAIN": "📖 코드 설명",
        "REVIEW": "🔍 코드 리뷰",
        "GENERATE": "✨ 코드 생성",
        "REFACTOR": "🔧 리팩토링",
        "ERROR_FIX": "🛠️ 에러 수정",
        "ERROR_ANALYZE": "🔥 에러 분석",
        "GENERATE_DOC": "📝 문서 생성",
        "GENERATE_TEST": "🧪 테스트 생성",
        "SEARCH": "🔎 검색",
        "QUESTION": "💬 답변",
        "CLARIFY": "❓ 확인 질문",
        "CHITCHAT": "😊 대화",
        "CHITCHAT_WEATHER": "☁️ 날씨 대화",
        "CHITCHAT_TIME": "⏰ 시간 대화",
    }
    return labels.get(intent, "💬")


def _parse_tasks(route_json: str) -> list:
    """LLM 라우팅 응답에서 tasks 배열 추출"""
    try:
        raw = route_json.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            return parsed.get("tasks", [])
    except (json.JSONDecodeError, KeyError):
        pass
    return []


# DevelopmentIntentClassifier.VALID_INTENTS와 동기화
from app.intent.development import DevelopmentIntentClassifier as _DevCls
_VALID_INTENTS = _DevCls.VALID_INTENTS

# ── 고속 분류 키워드 패턴 (모듈 레벨 1회 컴파일) ──
import re as _re_fast

# 에러/예외 패턴 (Eclipse 콘솔 스택트레이스 등)
_FAST_ERROR_KW = _re_fast.compile(
    r'Exception|에러|오류|에외|error|Caused\s*by|Stacktrace|스택트레이스'
    r'|at\s+\w+\.\w+\.\w+\(|\.java:\d+'
    r'|NullPointerException|ClassCastException|IllegalArgumentException'
    r'|ArrayIndexOutOfBoundsException|NumberFormatException'
    r'|BeanCreationException|NoSuchBeanDefinitionException'
    r'|LazyInitializationException|DataAccessException'
    r'|ORA-\d+|SQLException|IOException|FileNotFoundException'
    r'|ClassNotFoundException|NoClassDefFoundError'
    r'|OutOfMemoryError|StackOverflowError'
    r'|HttpMessageNotReadableException|MethodArgumentNotValidException'
    r'|AccessDeniedException|AuthenticationException'
    r'|ServletException|jsp|SEVERE|WARN.*exception'
    r'|root\s*cause|근본\s*원인|원인.*분석',
    _re_fast.IGNORECASE
)
# 에러 수정 요청 패턴
_FAST_ERROR_FIX_KW = _re_fast.compile(r'고쳐|수정해|fix|해결해|바꿔.*에러|에러.*바꿔', _re_fast.IGNORECASE)
# 코드 수정 키워드
_FAST_MODIFY_KW = _re_fast.compile(r'추가|수정|변경|삭제|생성|구현|만들어|넣어|바꿔|고쳐|적용|작성|개발')
# 코드 설명 키워드
_FAST_EXPLAIN_KW = _re_fast.compile(r'설명|분석|살펴|알려|뭐야|뭔지|구조|역할|왜|어떻게|차이')
# 일반 정보/지식 키워드
_FAST_INFO_KW = _re_fast.compile(r'정석|장점|단점|원칙|비교|장단점|패턴|SRP|SOLID|리팩토링|테스트|유지보수|보안')
# 일상 대화/잡담 키워드 (MyAI casual_keywords 통합)
_FAST_CHITCHAT_KW = _re_fast.compile(
    # 인사/안부
    r'^\s*(안녕|하이|헬로|hello|hi|hey|yo|반가워|잘\s*지냈|어떻게\s*지내|뭐\s*해|뭐하고\s*있|'
    # 감정/상태
    r'심심|지루|졸려|배고파|피곤|힘들어|외로워|우울|속상|짜증|기분\s*(좋|나쁘|별로)|'
    # 인사 마무리
    r'고마워|감사|수고|잘\s*자|bye|바이|다녀올게|다녀왔|퇴근|출근|'
    # 이모티콘/초성
    r'ㅎㅇ|ㅎㅎ|ㅋㅋ|ㄱㅅ|ㅎㅎㅎ|ㅠㅠ|ㅜㅜ|'
    # 시간대 인사
    r'좋은\s*아침|좋은\s*하루|점심|저녁|오후|하루|주말|월요일|금요일|불금|'
    # 캐릭터/잡담
    r'볼트|놀자|재밌|웃겨|힘내|화이팅|파이팅|응원|잘했어|수고했어|대박|'
    # 날씨 (단독 + 질문형)
    r'날씨\s*(어때|어떻|좋|나쁘|뭐야)?|오늘\s*날씨|내일\s*날씨|'
    r'비\s*(와|온|오|내려|올까)|바람\s*(불|세|강)|눈\s*(와|온|오|내려|올까)|'
    r'덥|춥|습|맑|흐리|차가워|더워|추워|'
    # 시간
    r'몇\s*시|지금\s*시간|오늘\s*날짜|무슨\s*요일|며칠)'
    r'(?:하세요|합니다|해요|하다|해|하|다|네|요|어|지|나|세|야|니|냐|죠)*'
    r'\s*[.!?~ㅋㅎ]*\s*$',
    _re_fast.IGNORECASE
)
# 날씨/시간 세부 패턴 (CHITCHAT 내 특수 처리용)
_CHITCHAT_WEATHER_KW = _re_fast.compile(
    r'날씨|비\s*(와|온|오|내려|올까)|바람\s*(불|세|강)|눈\s*(와|온|오|내려|올까)|덥|춥|습|맑|흐리|차가워|더워|추워|기온|온도',
    _re_fast.IGNORECASE
)
_CHITCHAT_TIME_KW = _re_fast.compile(
    r'몇\s*시|지금\s*시간|오늘\s*날짜|무슨\s*요일|며칠',
    _re_fast.IGNORECASE
)


def _fast_classify(message: str, has_code: bool, profile: str, question: str = "") -> tuple:
    """고속 키워드 기반 의도 분류 — (fast_intent, fast_files) 반환
    LLM 호출 없이 키워드 패턴 매칭으로 의도 분류."""
    fast_intent = None
    fast_files = []

    # ★ 일상 대화 우선 감지 (코드 없을 때만)
    if not has_code and _FAST_CHITCHAT_KW.search(message):
        # 날씨/시간 세부 태그 전달
        if _CHITCHAT_WEATHER_KW.search(message):
            fast_intent = "CHITCHAT_WEATHER"
        elif _CHITCHAT_TIME_KW.search(message):
            fast_intent = "CHITCHAT_TIME"
        else:
            fast_intent = "CHITCHAT"
        logger.info("[고속분류-잡담] %s → %s", message[:80], fast_intent)
        return fast_intent, []

    # ★ 에러 패턴 우선 감지
    if _FAST_ERROR_KW.search(message):
        if _FAST_ERROR_FIX_KW.search(message):
            fast_intent = "ERROR_FIX"
        else:
            fast_intent = "ERROR_ANALYZE"
        if profile:
            fast_files = _keyword_search_paths(message, profile, max_results=5)
        logger.info("[고속분류-에러] %s → %s", message[:80], fast_intent)
    elif has_code:
        fast_intent = None  # 코드 선택됨 → LLM 분류 필요
    elif profile and _FAST_MODIFY_KW.search(message):
        fast_intent = "SEARCH"
        fast_files = _keyword_search_paths(message, profile, max_results=15)
        if fast_files:
            fast_files = _expand_related_files(fast_files, profile, question=question)
    elif profile and _FAST_EXPLAIN_KW.search(message) and not _FAST_MODIFY_KW.search(message):
        fast_intent = "EXPLAIN"
        fast_files = _keyword_search_paths(message, profile, max_results=5)
    elif _FAST_INFO_KW.search(message):
        fast_intent = "QUESTION"

    return fast_intent, fast_files


def _dedup_tasks(tasks: list) -> list:
    """동일 intent + 유사 detail의 태스크를 하나로 합침 (비표준 intent → QUESTION 정규화)"""
    merged = []
    seen_intents = {}
    for t in tasks:
        intent = t.get("intent", "QUESTION")
        if intent not in _VALID_INTENTS:
            intent = "QUESTION"
            t["intent"] = intent
        if intent in seen_intents:
            # 기존 태스크에 files 합침
            existing = seen_intents[intent]
            for f in t.get("files", []):
                if f not in existing.get("files", []):
                    existing.setdefault("files", []).append(f)
            # detail이 더 길면 교체
            if len(t.get("detail", "")) > len(existing.get("detail", "")):
                existing["detail"] = t["detail"]
        else:
            seen_intents[intent] = t
            merged.append(t)
    return merged


def _extract_profile_paths(profile: str) -> list[str]:
    """프로필 텍스트에서 '경로:' 항목의 파일 경로 추출"""
    paths = []
    for line in profile.split("\n"):
        line = line.strip()
        if line.startswith("경로:") or line.startswith("경로 :"):
            raw = line.split(":", 1)[1].strip()
            # '경로: path | 설명: ...' 형태에서 path만 추출
            fp = raw.split("|", 1)[0].strip() if "|" in raw else raw
            fp = fp.replace("\\", "/")  # Windows 경로 정규화
            if fp and not fp.startswith("설명"):
                paths.append(fp)
    return paths


def _keyword_search_paths(question: str, profile: str, max_results: int = 5) -> list[str]:
    """사용자 질문에서 키워드를 추출하여 프로필 경로를 직접 검색 (LLM 불필요)

    myai intent_classifier 스타일: 패턴 우선 → 키워드 확장. 문맥 기반 해석 적용.
    예: "faq 게시판에 메일 항목 추가" → faq, board, mail
    예: "게시판 관련된 파일 안내해줘" → board만 (파일=소스목록, file/attach 제외)
    """
    profile_paths = _extract_profile_paths(profile)
    if not profile_paths:
        return []

    import re
    q = question.strip()
    q_lower = q.lower()

    # [myai 스타일] 패턴 기반 문맥 감지 — "XX 관련된 파일" / "XX 관련 파일"
    # ★ 프로필 기반 도메인 맵 → "XX 관련 파일" 패턴에서 도메인 키워드 추출
    _prof_domain_map = _build_domain_map(profile) if profile else []
    _prof_domain_priority_map = {}  # {한글키워드: [영문키워드들]}
    for dm in _prof_domain_map:
        for kw in dm["keywords"]:
            en_kws = [w.lower() for f in dm["files"] for w in re.findall(r'[a-zA-Z]{3,}', f.rsplit('.', 1)[0]) 
                      if w.lower() not in ("controller", "service", "serviceimpl", "impl", "dao", "list", "view", "write", "update", "regist", "proc")]
            if en_kws:
                _prof_domain_priority_map[kw] = list(set(en_kws))[:5]

    related_file_pattern = re.search(
        r'([가-힣A-Za-z]{2,})\s*(관련된|관련)\s*파일', q
    )
    domain_priority = []
    if related_file_pattern:
        _matched_kw = related_file_pattern.group(1).lower()
        domain_priority = _prof_domain_priority_map.get(_matched_kw, [])
        if not domain_priority:
            for kw, en_kws in _prof_domain_priority_map.items():
                if kw in _matched_kw or _matched_kw in kw:
                    domain_priority = en_kws
                    break

    # 한글→영문 변환 맵 (기본 + 프로필 기반 동적 추가)
    kr_to_en = {
        "게시판": "board", "공지": "notice", "자유": "free",
        "결제": "pay", "회원": "member,cust", "상품": "product,goods",
        "주문": "order", "포인트": "point", "쿠폰": "coupon",
        "장바구니": "cart", "배송": "delivery,dlvry", "로그인": "login",
        "관리자": "admin", "설정": "config,setting", "메뉴": "menu",
        "권한": "auth", "메일": "mail,email",
        "이벤트": "event", "통계": "stat", "댓글": "comment,reply",
        "카테고리": "category,cate", "검색": "search", "리뷰": "review",
        "승인": "approval",
    }
    # 프로필에서 추출한 도메인 키워드를 kr_to_en에 동적 추가
    for kw, en_kws in _prof_domain_priority_map.items():
        if kw not in kr_to_en:
            kr_to_en[kw] = ",".join(en_kws[:3])

    # 1단계: 영문 키워드 추출 (3글자 이상)
    import re
    en_words = re.findall(r'[a-zA-Z]{3,}', question.lower())
    # 2단계: 한글 키워드 → 영문 변환
    for kr, en in kr_to_en.items():
        if kr in question:
            en_words.extend(en.split(","))
    # "파일" → file,attach는 업로드/다운로드 문맥에서만. "게시판 관련된 파일"은 소스파일 목록이므로 제외
    if "파일" in question and any(w in question for w in ("업로드", "다운로드", "첨부", "용량", "제한", "크기")):
        en_words.extend(["file", "attach"])

    # 3단계: 질문에서 의미 있는 한글 명사도 파일 경로에 직접 매칭 시도
    kr_words = re.findall(r'[가-힣]{2,}', question)

    if not en_words:
        return []

    # 불용어 제거 (faq, mail 등 도메인 키워드는 유지)
    stop_words = {"the", "and", "for", "from", "with", "java", "xml", "jsp",
                  "src", "main", "impl", "dao", "web",
                  "resources", "추가", "수정", "변경", "삭제", "해줘", "부탁",
                  "있게", "없게", "항목", "필드", "컬럼", "기능"}
    en_words = [w for w in en_words if w not in stop_words]

    if not en_words:
        return []

    # domain_priority 있을 시 해당 키워드 추가 (패턴 "XX 관련된 파일" 등)
    if domain_priority:
        for kw in domain_priority:
            if kw not in en_words:
                en_words.append(kw)

    # UI 수정(메일/필드/항목 추가) 시 JSP 가산
    ui_mod = "추가" in q or "항목" in q or "필드" in q
    # 프로필 경로에서 키워드 매칭 + 점수 계산
    scored = []
    for p in profile_paths:
        p_lower = p.lower()
        score = 0
        fname = p_lower.rsplit("/", 1)[-1] if "/" in p_lower else p_lower
        for kw in en_words:
            if kw in p_lower:
                if kw in fname:
                    score += 3
                else:
                    score += 1
        if score > 0:
            if ui_mod and fname.endswith(".jsp"):
                score += 2
            # [myai 스타일] "XX 관련된 파일" 패턴 시: 해당 도메인 파일 가산, file/attach 계열 감점
            if domain_priority:
                if any(d in fname for d in domain_priority):
                    score += 4
                if any(x in fname for x in ("upload", "attach", "multipart")) and not any(d in fname for d in domain_priority):
                    score -= 3  # 파일 업로드 관련은 "XX 관련된 파일" 질문에서 제외 (해당 도메인 키워드 없으면)
            # 프로필 기반 도메인 매칭 — 매칭된 기능 블록의 파일은 가산, 다른 도메인은 감점
            _domain_files = _match_question_to_domain(question, _build_domain_map(profile))
            if _domain_files:
                _df_names = set((df.rsplit('/', 1)[-1] if '/' in df else df).lower() for df in _domain_files)
                if fname in _df_names or any(fname in df.lower() for df in _domain_files):
                    score += 5
                else:
                    _domain_kws = set()
                    for df in _domain_files:
                        _dfn = (df.rsplit('/', 1)[-1] if '/' in df else df).lower()
                        _domain_kws.update(re.findall(r'[a-z]{3,}', _dfn.rsplit('.', 1)[0]))
                    if not any(dk in fname for dk in _domain_kws):
                        score -= 3
            scored.append((score, p))

    scored.sort(key=lambda x: (-x[0], x[1]))
    # 다중 키워드(예: faq+메일) 질문이면 결과 수 확대
    limit = max(max_results, 10) if len(en_words) >= 2 else max_results
    # 최소 점수 2 이상만 채택 (파일명에 키워드 1개=3점, 경로에만=1점)
    result = [p for s, p in scored if s >= 2][:limit]
    if result:
        logger.info("[키워드검색] 질문='%s' → 키워드=%s → %d개 파일: %s",
                    question[:50], en_words[:12], len(result),
                    [f.rsplit('/', 1)[-1] for f in result])
    return result


def _fuzzy_file_match(name_a: str, name_b: str) -> bool:
    """두 파일명이 유사한지 확인 (예: BoardFaqController.java ↔ FaqController.java)"""
    a = name_a.lower().replace(".java", "").replace(".xml", "").replace(".jsp", "")
    b = name_b.lower().replace(".java", "").replace(".xml", "").replace(".jsp", "")
    ext_a = name_a.rsplit(".", 1)[-1].lower() if "." in name_a else ""
    ext_b = name_b.rsplit(".", 1)[-1].lower() if "." in name_b else ""
    if ext_a != ext_b:
        return False
    # 한쪽이 다른쪽을 포함하면 매칭
    if a in b or b in a:
        return True
    return False


_project_js_keywords_cache: set = set()

def _is_static_library_file(path: str, question: str = "", profile: str = None) -> bool:
    """정적 라이브러리 파일 여부 판단 — CKEditor/jQuery 로케일 등 사용자 코드가 아닌 파일 제외
    프로필에서 프로젝트 고유 키워드를 추출하여 오판 방지"""
    p = path.lower().replace("\\", "/")
    q = (question or "").lower()
    if any(lib in q for lib in ("ckeditor", "jquery", "datepicker", "로케일", "locale", "i18n")):
        return False
    _lib_patterns = (
        "/ckeditor/", "/jquery-ui/", "/node_modules/", "/vendors/", "/vendor/lib/",
        "/tinymce/", "/summernote/", "/codemirror/", "/bootstrap/js/", "/moment/locale/",
    )
    if any(pat in p for pat in _lib_patterns):
        fn = p.rsplit("/", 1)[-1] if "/" in p else p
        if fn.endswith(".js"):
            # 프로젝트 고유 키워드: 프로필 기반 동적 추출 + 기본 예외
            global _project_js_keywords_cache
            if not _project_js_keywords_cache and profile:
                dm = _build_domain_map(profile)
                for block in dm:
                    for f in block.get("files", []):
                        words = re.findall(r'[a-z]{3,}', f.lower().rsplit('.', 1)[0])
                        _project_js_keywords_cache.update(
                            w for w in words if w not in ('controller', 'service', 'serviceimpl', 'impl', 'dao', 'list', 'view', 'write'))
                _project_js_keywords_cache.update(("custom",))
            project_kws = _project_js_keywords_cache if _project_js_keywords_cache else {"board", "faq", "mail", "admin", "custom"}
            if not any(kw in fn for kw in project_kws):
                return True
    _locale_patterns = ("/lang/", "/i18n/", "/locales/", "/nls/")
    if any(pat in p for pat in _locale_patterns):
        fn = p.rsplit("/", 1)[-1] if "/" in p else p
        if fn.endswith(".js") and len(fn) <= 12:
            return True
    return False


def _fix_file_paths(llm_files: list[str], profile: str, question: str = "") -> list[str]:
    """LLM이 반환한 파일 경로를 프로필의 실제 경로와 대조하여 교정"""
    profile_paths = _extract_profile_paths(profile)
    if not profile_paths:
        return llm_files

    fixed = []
    matched_dirs = set()  # 매칭된 파일의 디렉토리 수집

    # LLM이 "등", "프로필 경로" 같은 설명을 paths에 넣은 경우 — 키워드 확장 시 과다 매칭 방지
    _garbage = re.compile(r'등\s*프로필|프로필에\s*실제|경로\s*만|또는\s*\w+\.(java|jsp|xml)', re.I)

    for llm_fp in llm_files:
        llm_name = llm_fp.rsplit("/", 1)[-1].lower() if "/" in llm_fp else llm_fp.lower()
        # 쓰레기 출력(한글 설명이 섞인 경우)은 3차 키워드 확장 스킵
        skip_keyword_expand = bool(_garbage.search(llm_fp)) or (len(llm_fp) > 60 and any(c in llm_fp for c in "등경로실제"))

        # 1차: 정확한 파일명 매칭
        exact = [p for p in profile_paths if (p.rsplit("/", 1)[-1].lower() if "/" in p else p.lower()) == llm_name]
        if exact:
            for e in exact:
                if e not in fixed:
                    fixed.append(e)
                    if "/" in e:
                        matched_dirs.add(e.rsplit("/", 1)[0])
            continue

        # 2차: 퍼지 매칭 (BoardFaqController → FaqController)
        fuzzy = [p for p in profile_paths
                 if _fuzzy_file_match(llm_name, p.rsplit("/", 1)[-1].lower() if "/" in p else p.lower())]
        if fuzzy:
            for f in fuzzy:
                if f not in fixed:
                    fixed.append(f)
                    if "/" in f:
                        matched_dirs.add(f.rsplit("/", 1)[0])
            continue

        # 3차: 키워드 매칭 — 모든 키워드가 매칭되는 경우만 허용 (단일 키워드 매칭 방지)
        if skip_keyword_expand:
            logger.info("[파일교정] 설명형 출력 무시(키워드 확장 스킵): %s", llm_fp[:50])
            continue
        kw_raw = llm_fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] if "." in llm_fp else llm_fp
        keywords = re.findall(r'[A-Z][a-z]+|[a-z]+', kw_raw)
        keywords = [k.lower() for k in keywords if len(k) > 2 and k.lower() not in ("java", "xml", "jsp", "impl", "src", "main", "web", "admin")]
        if keywords and len(keywords) >= 2:
            min_score = len(keywords)
            scored = []
            for p in profile_paths:
                p_lower = p.lower()
                score = sum(1 for k in keywords if k in p_lower)
                if score >= min_score:
                    scored.append((score, p))
            scored.sort(key=lambda x: -x[0])
            if scored:
                for _, p in scored[:2]:
                    if p not in fixed:
                        fixed.append(p)
                        if "/" in p:
                            matched_dirs.add(p.rsplit("/", 1)[0])
                logger.info("[파일교정] 3차 키워드 매칭: %s → kw=%s → %s", llm_fp, keywords, [s[1].rsplit('/',1)[-1] for s in scored[:2]])
                continue

        # 매칭 실패 → 환각 파일이므로 제거
        logger.info("[파일교정] 환각 파일 제거: %s (kw=%s)", llm_fp, keywords if keywords else "없음")

    # 4차: 관련 디렉토리 확장 — 비활성화 (과도한 파일 추가 방지)
    # LLM이 정확한 파일을 못 찾으면 환각 파일을 추가하는 것보다 없는 게 나음

    # 5차: 정적 라이브러리/로케일 파일 제거 + 같은 파일명 중복 제거
    before_filter = len(fixed)
    filtered = []
    seen_names = set()
    for fp in fixed:
        if _is_static_library_file(fp, question, profile):
            continue
        fn = (fp.rsplit("/", 1)[-1] if "/" in fp else fp).lower()
        if fn in seen_names:
            continue
        seen_names.add(fn)
        filtered.append(fp)
    if len(filtered) < before_filter:
        logger.info("[파일교정] 라이브러리/중복 제거: %d → %d개", before_filter, len(filtered))
    logger.info("[파일교정] LLM=%s → 교정=%s", [f.rsplit('/',1)[-1] for f in llm_files[:15]], [f.rsplit('/',1)[-1] for f in filtered])
    return filtered


def _expand_related_files(files: list[str], profile: str, question: str = "") -> list[str]:
    """프로필의 기능 요약에서 같은 기능 그룹의 관련 파일을 자동 확장
    
    예: FaqController.java 하나만 찾아도 → BoardServiceImpl, faqList.jsp 등 자동 추가
    질문에 "추가/항목/필드" 있으면 JSP 화면 필수 포함.
    """
    if not files or not profile:
        return files

    import re
    profile_paths = _extract_profile_paths(profile)
    if not profile_paths:
        return files

    # 기능 요약 섹션만 추출 (## 🔍 ~ 다음 ## 전)
    summary_match = re.search(
        r'(## 🔍 프로젝트 기능 요약.*?)(?=\n## [^🔍]|\n# )',
        profile, re.DOTALL
    )
    summary_section = summary_match.group(1) if summary_match else profile[:5000]

    # 기능 요약 내 각 기능 블록 파싱
    feature_blocks = re.split(r'(?=####\s+\d+\.)', summary_section)
    
    # 이미 가진 파일의 클래스명/파일명 키워드 추출
    # "admin" 제외 — mybatis_sql_admin_* 가 승인/관리자 블록까지 매칭시켜 과다 확장됨
    _exclude = ('java', 'xml', 'jsp', 'impl', 'service', 'controller', 'dao', 'mapper', 'sql', 'www', 'mybatis', 'admin')
    file_keywords = set()
    specific_keywords = set()
    for fp in files:
        fname = fp.rsplit('/', 1)[-1] if '/' in fp else fp
        base = fname.rsplit('.', 1)[0].lower()
        file_keywords.add(base)
        words = re.findall(r'[A-Z][a-z]+|[a-z]+', fname.rsplit('.', 1)[0])
        for w in words:
            wl = w.lower()
            if len(wl) > 2 and wl not in _exclude:
                file_keywords.add(wl)
                specific_keywords.add(wl)

    # ★ 프로필 기반 도메인 맵 — 기능 블록에서 자동 파악 (하드코딩 없이 프로필만 사용)
    q_lower = (question or "").lower()
    domain_map = _build_domain_map(profile) if profile else []
    matched_domain_files = _match_question_to_domain(question, domain_map)
    has_domain_match = bool(matched_domain_files)
    domain_block_keywords = set()
    if matched_domain_files:
        for fp in matched_domain_files:
            fn = (fp.rsplit('/', 1)[-1] if '/' in fp else fp).lower()
            words = re.findall(r'[a-z]{3,}', fn.rsplit('.', 1)[0])
            domain_block_keywords.update(words)

    expanded = list(files)
    matched_features = []

    # 1단계: 기능 요약 블록에서 처리 흐름 기반 파일 확장
    for block in feature_blocks:
        if not block.strip():
            continue
        block_lower = block.lower()

        is_related = any(kw in block_lower for kw in file_keywords)
        if not is_related:
            continue
        # 도메인 매칭된 질문인데 블록에 관련 키워드 없으면 스킵
        if has_domain_match and not any(dk in block_lower for dk in domain_block_keywords):
            continue

        matched_features.append(block.split('\n')[0][:60].strip())

        # 처리 흐름에서 Java 클래스 추출
        flow_classes = re.findall(r'([A-Z][a-zA-Z]+(?:Controller|Service|ServiceImpl|Impl|Dao|DAO))', block)
        xml_files = re.findall(r'(mybatis_[\w]+\.xml|[\w]+_sql_[\w]+\.xml)', block)
        jsp_files = re.findall(r'([\w]+\.jsp)', block)

        # 도메인 필터: 프로필 기능 블록에 속한 파일만 허용
        def _domain_ok(name: str) -> bool:
            if not matched_domain_files:
                return True
            n = name.lower()
            return any(n in df.lower() or df.lower().endswith(n) for df in matched_domain_files)

        java_xml_mentions = set()
        for cls in flow_classes:
            if _domain_ok(cls):
                java_xml_mentions.add(cls.lower() + '.java')
        for x in xml_files:
            if not has_domain_match or _domain_ok(x):
                java_xml_mentions.add(x.lower())

        for mention in java_xml_mentions:
            for pp in profile_paths:
                pp_fname = (pp.rsplit('/', 1)[-1] if '/' in pp else pp).lower()
                if pp_fname == mention or mention in pp_fname:
                    if pp not in expanded and len(expanded) < 15:
                        expanded.append(pp)

        # JSP → 구체적 키워드(faq, cart 등)가 JSP 파일명에 포함된 것만
        for jsp in jsp_files:
            jsp_lower = jsp.lower()
            if any(sk in jsp_lower for sk in specific_keywords):
                for pp in profile_paths:
                    pp_fname = (pp.rsplit('/', 1)[-1] if '/' in pp else pp).lower()
                    if pp_fname == jsp_lower:
                        if pp not in expanded and len(expanded) < 15:
                            expanded.append(pp)

    # 2단계: 패키지 구조 기반 보충 — 처리 흐름에서 언급된 파일의 인터페이스/DAO 추가
    # 예: BoardServiceImpl → BoardService.java, BoardDao.java
    expanded_names = set()
    for fp in expanded:
        fname = (fp.rsplit('/', 1)[-1] if '/' in fp else fp).lower()
        base = fname.rsplit('.', 1)[0]
        expanded_names.add(base)
        # ServiceImpl → 인터페이스(Service)와 DAO도 찾기
        if 'serviceimpl' in base:
            # boardserviceimpl → boardservice, boarddao
            svc_name = base.replace('serviceimpl', 'service')
            dao_name = base.replace('serviceimpl', 'dao')
            expanded_names.add(svc_name)
            expanded_names.add(dao_name)

    for pp in profile_paths:
        if pp in expanded or len(expanded) >= 15:
            continue
        pp_fname = (pp.rsplit('/', 1)[-1] if '/' in pp else pp).lower()
        pp_base = pp_fname.rsplit('.', 1)[0]
        if pp_base in expanded_names and pp not in expanded:
            expanded.append(pp)

    # 3단계: 도메인 매칭된 파일에서 관련 보조 파일(mail 등) 추가 (프로필 기반)
    fnames_lower = [(f.rsplit('/', 1)[-1] if '/' in f else f).lower() for f in files]
    _prof_dm = _build_domain_map(profile) if profile else []
    _domain_related_kws = set()  # 도메인 블록에서 추출한 파일명 키워드
    _matched_domain = _match_question_to_domain(question, _prof_dm)
    if _matched_domain:
        for df in _matched_domain:
            dfn = (df.rsplit('/', 1)[-1] if '/' in df else df).lower()
            words = re.findall(r'[a-z]{3,}', dfn.rsplit('.', 1)[0])
            _domain_related_kws.update(w for w in words
                                       if w not in ('controller', 'service', 'serviceimpl', 'impl', 'dao', 'list', 'view'))

    # 4단계: JSP 화면 보강 — UI 변경 시 도메인 매칭 JSP 필수 (프로필 기반 키워드)
    has_jsp = any((f.rsplit('/', 1)[-1] if '/' in f else f).lower().endswith('.jsp') for f in expanded)
    ui_mod_keywords = "추가" in question or "항목" in question or "필드" in question
    # 도메인 키워드는 프로필 기반 매칭에서 추출
    search_keywords = specific_keywords | _domain_related_kws
    if (not has_jsp or ui_mod_keywords) and len(expanded) < 15:
        for pp in profile_paths:
            if pp in expanded or len(expanded) >= 15:
                break
            pp_fname = (pp.rsplit('/', 1)[-1] if '/' in pp else pp).lower()
            if not pp_fname.endswith('.jsp'):
                continue
            if any(sk in pp_fname for sk in search_keywords):
                if pp not in expanded:
                    expanded.append(pp)
                    logger.info("[파일확장] JSP 화면 추가: %s", pp_fname)

    # 5단계: DAO가 있으면 관련 MyBatis SQL XML 자동 추가
    dao_keywords = set()
    for fp in expanded:
        fname = (fp.rsplit('/', 1)[-1] if '/' in fp else fp).lower()
        if 'dao' in fname or 'mapper' in fname:
            base = fname.replace('dao.java', '').replace('mapper.java', '').replace('.java', '')
            if base and len(base) > 2:
                dao_keywords.add(base)
    # 질문에서도 도메인 키워드 추출
    q_words = re.findall(r'[a-zA-Z]+', question or "")
    for w in q_words:
        if len(w) > 2:
            dao_keywords.add(w.lower())

    has_xml = any((f.rsplit('/', 1)[-1] if '/' in f else f).lower().endswith('.xml')
                  and 'sql' in (f.rsplit('/', 1)[-1] if '/' in f else f).lower()
                  for f in expanded)
    if dao_keywords and not has_xml and len(expanded) < 15:
        for pp in profile_paths:
            if pp in expanded or len(expanded) >= 15:
                break
            pp_fname = (pp.rsplit('/', 1)[-1] if '/' in pp else pp).lower()
            if pp_fname.endswith('.xml') and ('sql' in pp_fname or 'mybatis' in pp_fname or 'mapper' in pp_fname):
                if any(dk in pp_fname for dk in dao_keywords):
                    expanded.append(pp)
                    logger.info("[파일확장] MyBatis XML 추가: %s (DAO 키워드: %s)", pp_fname, dao_keywords)

    if len(expanded) > len(files):
        added = [f.rsplit('/',1)[-1] for f in expanded if f not in files]
        logger.info("[파일확장] 기능 요약에서 %d개 추가: %s (매칭: %s)",
                    len(added), added, matched_features)

    return expanded


def _sse(event: str, data: dict) -> str:
    """SSE 이벤트 문자열 생성"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_domain_map(profile: str) -> list[dict]:
    """프로필의 기능 요약 블록을 파싱하여 도메인 맵 생성.
    
    Returns: [
        {"title": "FAQ 관리", "keywords": ["faq", "관리"], "files": ["FaqController.java", "faqList.jsp", ...], "tables": ["TB_FAQ"]},
        {"title": "제휴문의", "keywords": ["제휴", "문의", "cooper"], "files": [...], "tables": [...]},
    ]
    """
    import re
    if not profile:
        return []
    
    summary_match = re.search(
        r'(## 🔍 프로젝트 기능 요약.*?)(?=\n---|\n# [^#]|\Z)',
        profile, re.DOTALL
    )
    if not summary_match:
        return []
    
    summary = summary_match.group(1)
    blocks = re.split(r'(?=####\s+\d+\.)', summary)
    
    domains = []
    for block in blocks:
        if not block.strip():
            continue
        title_match = re.match(r'####\s+\d+\.\s*(.+)', block.strip())
        if not title_match:
            continue
        title = title_match.group(1).strip()
        
        # 타이틀에서 키워드 추출 (한글 + 영문)
        kr_words = re.findall(r'[가-힣]{2,}', title)
        en_words = re.findall(r'[a-zA-Z]{3,}', title)
        keywords = [w.lower() for w in kr_words + en_words]
        
        # 처리 흐름에서 클래스명/파일명 추출
        files = []
        for m in re.finditer(r'([A-Z][a-zA-Z]+(?:Controller|Service|ServiceImpl|Impl|Dao|DAO))', block):
            files.append(m.group(1) + '.java')
        for m in re.finditer(r'([A-Za-z_]+\.(?:xml|jsp))', block):
            files.append(m.group(1))
        # 화면 항목에서 JSP 추출
        for m in re.finditer(r'(\w+\.jsp)', block, re.I):
            fn = m.group(1)
            if fn not in files:
                files.append(fn)
        
        # 테이블 추출
        tables = re.findall(r'(TB_[A-Z_]+)', block)
        
        # URL에서 도메인 키워드 추출
        for m in re.finditer(r'/(\w+)/\w+\.do', block):
            url_kw = m.group(1).lower()
            if url_kw not in keywords and len(url_kw) > 2:
                keywords.append(url_kw)
        
        if files:
            domains.append({
                "title": title,
                "keywords": list(set(keywords)),
                "files": list(set(files)),
                "tables": list(set(tables)),
            })
    
    if domains:
        logger.info("[도메인맵] %d개 기능 블록 파싱: %s", len(domains), 
                    [(d["title"], len(d["files"])) for d in domains])
    return domains


def _match_question_to_domain(question: str, domain_map: list[dict]) -> list[str]:
    """질문을 도메인 맵과 매칭하여 해당 기능 블록의 파일 목록 반환."""
    if not question or not domain_map:
        return []
    
    q_lower = question.lower()
    best_score = 0
    best_files = []
    
    for domain in domain_map:
        score = 0
        for kw in domain["keywords"]:
            if kw in q_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_files = domain["files"]
    
    if best_score >= 1:
        logger.info("[도메인매칭] '%s' → 파일 %d개: %s", question[:40], len(best_files), best_files[:5])
        return best_files
    return []


def _extract_entities_cheatsheet(file_contents: dict) -> str:
    """파일 내용에서 테이블명/컬럼명/메서드명 등 핵심 엔티티를 추출하여 치트시트 생성.
    LLM이 이름을 지어내지 않고 기존 코드의 실제 이름을 사용하도록 강제."""
    import re
    tables = set()
    columns = {}
    java_methods = set()
    sql_ids = set()
    form_fields = set()

    for path, content in file_contents.items():
        if not content:
            continue
        fl = path.lower()

        if fl.endswith('.xml') and ('sql' in fl or 'mybatis' in fl or 'mapper' in fl):
            # MyBatis XML → 테이블명, 컬럼명, SQL ID 추출
            for m in re.finditer(r'(?:FROM|INTO|UPDATE|JOIN)\s+([A-Z_][A-Z0-9_]+)', content, re.I):
                tbl = m.group(1).upper()
                if len(tbl) > 3 and tbl not in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'WHERE', 'VALUES', 'FROM'):
                    tables.add(tbl)
            for m in re.finditer(r'<(?:select|insert|update|delete)\s+id="([^"]+)"', content):
                sql_ids.add(m.group(1))
            for tbl in tables:
                tbl_cols = set()
                for m in re.finditer(r'(?:' + re.escape(tbl) + r'\.)?([A-Z_][A-Z0-9_]{2,})\s*[,=)\s]', content, re.I):
                    col = m.group(1).upper()
                    if col not in tables and col not in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'WHERE', 'VALUES', 'FROM', 'INTO', 'SET', 'AND', 'NULL'):
                        tbl_cols.add(col)
                if tbl_cols:
                    columns[tbl] = tbl_cols

        elif fl.endswith('.java'):
            # Java → public 메서드 시그니처 추출
            for m in re.finditer(r'public\s+\S+\s+(\w+)\s*\(', content):
                java_methods.add(m.group(1))

        elif fl.endswith('.jsp'):
            # JSP → form input name 추출
            for m in re.finditer(r'name="([^"]+)"', content):
                form_fields.add(m.group(1))

    if not tables and not java_methods:
        return ""

    parts = ["[★★★ 이 프로젝트의 실제 엔티티 — 반드시 이 이름들을 사용하세요!]"]
    if tables:
        for tbl in sorted(tables):
            cols = columns.get(tbl, set())
            if cols:
                parts.append(f"  테이블: {tbl} ({', '.join(sorted(cols)[:20])})")
            else:
                parts.append(f"  테이블: {tbl}")
    if sql_ids:
        parts.append(f"  SQL ID: {', '.join(sorted(sql_ids)[:15])}")
    if java_methods:
        parts.append(f"  Java 메서드: {', '.join(sorted(java_methods)[:20])}")
    if form_fields:
        parts.append(f"  JSP form 필드: {', '.join(sorted(form_fields)[:15])}")

    return "\n".join(parts) + "\n\n"


@router.post("/chat/smart/stream")
async def smart_chat_stream(req: SmartChatRequest, llm: LlmDep, emb: EmbeddingDep):
    """스마트 채팅 SSE 스트리밍 — 의도 분류 후 태스크별 토큰 스트리밍"""
    try:
        return await _smart_chat_stream_inner(req, llm, emb)
    except Exception as e:
        logger.error("smart_chat_stream 초기화 실패: %s", e, exc_info=True)
        # 초기 처리(분류/RAG/파일) 실패 시에도 SSE 형식으로 에러 반환
        async def error_stream():
            yield _sse("token", {"content": f"\n\n❌ 처리 준비 중 오류: {e}"})
            yield _sse("done", {})
        return StreamingResponse(error_stream(), media_type="text/event-stream")


async def _resolve_profile(req: SmartChatRequest):
    """프로필 해석: project_id 있으면 서버에서 로드, 없으면 req.profile"""
    if req.project_id:
        from app.config.settings import get_settings
        from app.service import profile_store
        settings = get_settings()
        uid = req.user_id or "default"
        content = profile_store.load_profile(settings, uid, req.project_id)
        if content:
            return content
    return req.profile or ""


async def _smart_chat_stream_inner(req: SmartChatRequest, llm: LlmDep, emb: EmbeddingDep):
    """smart_chat_stream 내부 구현"""
    _t0 = time.time()
    _msg = (req.message or "")[:80]
    _log_step(_t0, "요청수신", f"질문='{_msg}...' (profile={'있음' if req.profile or req.project_id else '없음'})")

    # ── 세션 자동 생성/관리 ──
    from app.service import chat_session_service as css
    _session_user = req.user_id or "default"
    _session_id = req.session_id or ""
    if not _session_id:
        _sess = css.create_session(_session_user, "")
        _session_id = _sess["session_id"]
        logger.info("[세션] 자동 생성 session_id=%s", _session_id)
    css.append_message(_session_user, _session_id, "user", req.message)

    _raw_history = css.build_history_from_request(
        _session_user, _session_id,
        req.history or [], req.selected_indices,
    )
    effective_history = _trim_history_if_context_shifted(req.message, _raw_history, req.profile)
    _log_step(_t0, "맥락처리", f"history_resolved={len(_raw_history)}건→trimmed={len(effective_history)}건")

    resolved_message, pronoun_meta = _resolve_pronouns_in_message(req.message, effective_history)
    if pronoun_meta.get('replaced') or pronoun_meta.get('subject_inferred'):
        logger.info("[스트림] 대명사 해석: '%s' → '%s'", req.message[:60], resolved_message[:60])
        effective_message = resolved_message
    else:
        effective_message = req.message

    # 효율 모드: 키워드 추출 → 벡터 검색 → LLM 1회만
    if req.use_efficient:
        from app.service.efficient_agent_service import (
            search_code_with_keywords,
            assemble_context,
            efficient_agent_stream,
        )
        user_id = req.user_id or "default"
        project_id = (req.project_id or "").strip() or None
        results = await search_code_with_keywords(
            emb, effective_message, user_id=user_id, project_id=project_id, top_k=12
        )
        ctx = assemble_context(results)
        async def eff_gen():
            yield _sse("status", {"message": f"검색 완료: {len(results)}개 조각 (효율 모드)", "step": "search_done"})
            async for token in efficient_agent_stream(llm, effective_message, ctx, history=effective_history):
                yield _sse("token", {"content": token})
            yield _sse("done", {})
        return StreamingResponse(eff_gen(), media_type="text/event-stream")

    # 프로필 해석 (서버 저장 프로젝트 또는 인라인)
    profile = await _resolve_profile(req)
    _log_step(_t0, "프로필해석", f"profile={len(profile or '')}자, project_id={req.project_id or '-'}")
    if req.project_id and not profile:
        logger.warning("[스트림] project_id=%s 프로필 없음 — 프로필 없이 진행", req.project_id)

    # 1차 호출에서 이미 분류된 tasks가 있으면 재사용 (LLM 재호출 방지)
    _t_cls = time.time()
    if req.tasks:
        tasks = _dedup_tasks(req.tasks)
        _log_step(_t0, "tasks재사용", f"분류 스킵, {len(tasks)}개 태스크")
        logger.info("[스트림] tasks 재사용 (분류 스킵), profile=%d자", len(profile or ""))
    else:
        has_code = bool(req.selected_code)

        # ★ 고속 분류: 키워드 기반 의도 분류 + 파일 선별 (LLM 호출 없음)
        fast_intent, fast_files = _fast_classify(effective_message, has_code, profile, req.message)

        if fast_intent and (fast_files or fast_intent in ("QUESTION", "ERROR_ANALYZE", "ERROR_FIX")):
            # ★ LLM 호출 없이 즉시 분류 완료
            tasks = [{"intent": fast_intent, "detail": effective_message, "files": fast_files[:15]}]
            _log_step(_t0, "고속분류", f"의도={fast_intent} 파일={len(fast_files)}개 {time.time()-_t_cls:.1f}s 소요")
            logger.info("[고속분류] %.1f초 — %s → %s, 파일 %d개: %s",
                        time.time() - _t_cls, effective_message[:50], fast_intent,
                        len(fast_files), [f.rsplit('/',1)[-1] for f in fast_files[:8]])

            if fast_intent == "QUESTION" or _FAST_INFO_KW.search(effective_message):
                for t in tasks:
                    t['files'] = t.get('files', [])[:2]
        else:
            # 고속 분류 실패 → LLM 분류 (프로필 압축 후 전송)
            if profile:
                _log_step(_t0, "LLM분류시작", f"profile={len(profile)}자")
                logger.info("[스트림] classify_and_route 시작 — profile=%d자, msg=%s", len(profile), effective_message[:60])
                route_json = await llm.classify_and_route(effective_message, profile, has_code)
                _log_step(_t0, "LLM분류완료", f"{time.time()-_t_cls:.1f}s 소요")
                logger.info("[스트림 분류 결과] %.1f초 소요 — %s → %s", time.time() - _t_cls, effective_message[:80], route_json[:200])
            else:
                route_json = '{"tasks":[{"intent":"QUESTION","detail":"' + effective_message.replace('"', '\\"') + '","files":[]}]}'

            tasks = _parse_tasks(route_json)
            if not tasks:
                tasks = [{"intent": "QUESTION", "detail": effective_message, "files": []}]
            tasks = _dedup_tasks(tasks)

            _raw_files = []
            for t in tasks:
                for f in t.get("files", []):
                    if f not in _raw_files:
                        _raw_files.append(f)
            if _raw_files and profile:
                _raw_files = _fix_file_paths(_raw_files, profile, req.message)
                _raw_files = _expand_related_files(_raw_files, profile, question=req.message)
                for t in tasks:
                    t["files"] = list(_raw_files)[:15]
                logger.info("[스트림 파일교정+확장] %s", [f.rsplit('/',1)[-1] for f in _raw_files])

    has_needed = any(t.get("files") for t in tasks)

    # 파일 0개이면 키워드 기반 직접 검색 (LLM pick_files 스킵)
    if not has_needed and profile:
        _t_kw = time.time()
        kw_files = _keyword_search_paths(req.message, profile, max_results=15)
        if kw_files:
            kw_files = _expand_related_files(kw_files, profile, question=req.message)
            for t in tasks:
                t["files"] = kw_files[:15]
            has_needed = True
            _log_step(_t0, "키워드폴백", f"{len(kw_files)}개 파일, {time.time()-_t_kw:.1f}s")
            logger.info("[키워드 폴백] %d개 파일 발견", len(kw_files))

    # 키워드도 실패 → LLM pick_files (최후 수단)
    if not has_needed and profile:
        _t_pick = time.time()
        _log_step(_t0, "LLM_pick_files시작", "")
        detail_text = " ".join(t.get("detail", "") for t in tasks)
        picked_csv = await llm.pick_files(detail_text or req.message, profile)
        _log_step(_t0, "LLM_pick_files완료", f"{time.time()-_t_pick:.1f}s")
        if picked_csv and picked_csv.strip() != "NONE":
            fallback_files = []
            for line in picked_csv.strip().split("\n"):
                fp = line.strip()
                if fp and fp not in fallback_files and len(fallback_files) < 20:
                    if not _is_static_library_file(fp, req.message, profile):
                        fallback_files.append(fp)
            if fallback_files:
                fallback_files = _fix_file_paths(fallback_files, profile, req.message)
                fallback_files = _expand_related_files(fallback_files, profile, question=req.message)
                if fallback_files:
                    for t in tasks:
                        t["files"] = fallback_files[:15]
                    has_needed = True

    # tasks가 이미 확정된 2차 호출이면 file_contents가 비어도 스트리밍 진행
    if not req.file_contents and has_needed and not req.tasks:
        needed_files = []
        for t in tasks:
            for f in t.get("files", []):
                if f not in needed_files:
                    needed_files.append(f)
        _log_step(_t0, "파일요청반환", f"needed_files={len(needed_files)}개 (클라이언트가 읽어서 재호출)")
        return NoriResponse(data={
            "phase": "need_files",
            "tasks": tasks,
            "needed_files": needed_files[:15],
        })

    # RAG 컨텍스트 (프로젝트 프로필 임베딩 + 공통 컬렉션)
    rag_context = ""
    if req.use_rag and emb.is_available:
        _t_rag = time.time()
        _log_step(_t0, "RAG검색시작", "프로필임베딩+공통컬렉션")
        try:
            results = await emb.search(query=req.message, top_k=5)
            if req.project_id and req.user_id:
                try:
                    profile_results = await emb.search_profiles(
                        req.message, req.user_id, req.project_id, top_k=5
                    )
                    if profile_results:
                        results = profile_results + results
                        results = results[:8]
                except Exception:
                    pass
        except Exception as e:
            logger.warning("RAG 검색 실패 (무시하고 계속): %s", e)
            results = []
        _log_step(_t0, "RAG검색완료", f"{len(results)}개 결과, {time.time()-_t_rag:.1f}s")
        if results:
            # 임베딩 검색 결과 — 참고용만, 수정·출력 금지임을 명시
            rag_context = "\n---\n".join(
                f"[임베딩-참고용] {r.get('collection','')}\n{r['text'][:600]}" for r in results
            )

    # 파일 컨텍스트 — 스마트 청킹으로 큰 파일도 관련 부분만 추출
    _MAX_FILE_CTX = 20000  # 파일 컨텍스트 총 합계 최대 글자수
    _MAX_PER_FILE = 6000   # 파일 1개당 최대 글자수
    _chunk_infos = []      # 청크 읽기 진행 표시용
    _t_fctx = time.time()
    if req.file_contents:
        from app.service.file_chunker import smart_chunk_file
        parts = []
        total = 0
        _query_hint = " ".join(t.get("detail", req.message) for t in tasks)
        for path, content in req.file_contents.items():
            chunked, _cmeta = smart_chunk_file(
                file_path=path,
                content=content,
                query=_query_hint,
                max_chars=_MAX_PER_FILE,
                return_meta=True,
            )
            _short = path.rsplit("/", 1)[-1] if "/" in path else path
            if _cmeta["chunked"]:
                _chunk_infos.append(f"📖 {_short}: {_cmeta['total_lines']}줄 → {_cmeta['selected_chunks']}/{_cmeta['total_chunks']} 블록 선택")
            else:
                _chunk_infos.append(f"📄 {_short}: {_cmeta['total_lines']}줄 전체 읽기")
            part = f"### {path}\n```\n{chunked}\n```"
            if total + len(part) > _MAX_FILE_CTX:
                parts.append(f"\n// ... 나머지 파일 생략 (컨텍스트 크기 제한)")
                break
            parts.append(part)
            total += len(part)
        file_ctx = "\n\n".join(parts)
        _log_step(_t0, "파일컨텍스트", f"{len(req.file_contents)}개 파일→{len(file_ctx)}자, {time.time()-_t_fctx:.1f}s")
    else:
        file_ctx = ""
        _log_step(_t0, "파일컨텍스트", "file_contents 없음")
        logger.warning("[스트림] file_contents 비어있음! tasks=%s", 
                      [t.get("intent","?") + ":" + ",".join(t.get("files",[])) for t in tasks])

    async def event_stream():
        _full_response = []  # AI 응답 수집용
        try:
            task_labels = ", ".join(
                _intent_label(t.get("intent", "QUESTION")) for t in tasks
            )
            yield _sse("status", {"step": "plan", "message": task_labels, "tasks": tasks, "session_id": _session_id})

            # 청크 읽기 진행 표시
            if _chunk_infos:
                yield _sse("status", {"step": "file_read", "message": "\n".join(_chunk_infos)})

            # ── ReAct 모드: 전체 파일 컨텍스트 전달 + 의도별 스트리밍 ──
            _entities_cheatsheet = _extract_entities_cheatsheet(req.file_contents) if req.file_contents else ""
            if _entities_cheatsheet:
                logger.info("[스트림] 엔티티 치트시트 전달 — 테이블/메서드명 %d자", len(_entities_cheatsheet))
            multi = len(tasks) > 1
            for idx, t in enumerate(tasks):
                intent = t.get("intent", "QUESTION")
                detail = t.get("detail", req.message)
                code = req.selected_code or file_ctx
                label = _intent_label(intent)
                _log_step(_t0, f"태스크_{idx+1}", f"시작 {intent}")

                yield _sse("status", {
                    "step": "task_start", "index": idx,
                    "intent": intent, "message": f"{label} 처리 중...",
                })

                if multi:
                    yield _sse("token", {
                        "content": f"\n\n## {label} {idx + 1}. {detail}\n\n",
                    })

                try:
                    _t1 = time.time()
                    token_count = 0
                    _task_tokens: list[str] = []  # 은닉 검증용 per-task 응답 수집
                    async for token in llm.stream_task(
                        intent, detail, code, rag_context, file_ctx, effective_history,
                        profile=profile, entities_cheatsheet=_entities_cheatsheet
                    ):
                        if token_count == 0:
                            logger.info("[스트림] 첫 토큰 도착 — %.1f초 대기", time.time() - _t1)
                            yield _sse("status", {"step": "react_thought", "message": "💭 사고 중..."})
                        token_count += 1
                        _task_tokens.append(token)
                        _full_response.append(token)
                        yield _sse("token", {"content": token})
                    _log_step(_t0, f"태스크_{idx+1}", f"AI완료 {intent} {token_count}토큰 {time.time()-_t1:.1f}s")
                    logger.info("[스트림] 완료 — %d토큰, %.1f초 소요", token_count, time.time() - _t1)
                    if token_count == 0:
                        yield _sse("token", {"content": "\n\n⚠️ AI 모델이 응답을 생성하지 못했습니다. 다시 시도해주세요."})

                    # ── 은닉 검증 (Hidden Auto-Validation) ──
                    _VALIDATE_INTENTS = {"SEARCH", "ERROR_FIX", "REFACTOR", "GENERATE"}
                    if intent in _VALIDATE_INTENTS and req.file_contents and _task_tokens:
                        _primary_file = (t.get("files") or [None])[0]
                        _primary_content = req.file_contents.get(_primary_file, "") if _primary_file else ""
                        if _primary_file and _primary_content:
                            try:
                                from app.service.code_validator import validate_ai_output
                                _val = validate_ai_output(
                                    _primary_file, _primary_content, "".join(_task_tokens)
                                )
                                if not _val.passed:
                                    _errs = "\n".join(f"- {e}" for e in _val.errors)
                                    logger.warning(
                                        "[은닉검증] %s 실패 — 오류 %d개: %s",
                                        _primary_file, len(_val.errors), _val.errors,
                                    )
                                    yield _sse("status", {
                                        "step": "hidden_validate",
                                        "message": "🔍 코드 자동 검증 후 보완 중...",
                                    })
                                    _retry_q = (
                                        f"{detail}\n\n"
                                        f"⚠️ 자동 검수 결과 다음 구조적 오류가 발견되었습니다:\n{_errs}\n\n"
                                        f"위 오류를 반드시 수정하여 올바른 코드를 재작성하세요. "
                                        f"원본 파일 `{_primary_file}`의 package, 클래스명, "
                                        f"@RequestMapping URL은 그대로 유지하세요."
                                    )
                                    _corrected = await llm.chat(
                                        question=_retry_q,
                                        history=effective_history,
                                        rag_context=rag_context,
                                        project_context=file_ctx,
                                    )
                                    if _corrected:
                                        _sep = (
                                            "\n\n---\n"
                                            "> 🔧 **자동 수정됨** (코드 검증에서 구조적 오류 발견 → 재생성)\n\n"
                                        )
                                        for _chunk in [_sep, _corrected]:
                                            _full_response.append(_chunk)
                                            yield _sse("token", {"content": _chunk})
                            except Exception as _ve:
                                logger.warning("[은닉검증] 오류 (무시하고 계속): %s", _ve)
                except Exception as e:
                    logger.error("stream_task 실패: %s", e, exc_info=True)
                    yield _sse("token", {"content": f"\n\n❌ {intent} 실행 실패: {e}"})

                if multi and idx < len(tasks) - 1:
                    yield _sse("token", {"content": "\n\n---\n\n"})
        except Exception as e:
            logger.error("event_stream 예외: %s", e, exc_info=True)
            yield _sse("token", {"content": f"\n\n❌ 스트리밍 오류: {e}"})
        finally:
            # AI 응답 세션에 저장
            if _full_response:
                try:
                    css.append_message(_session_user, _session_id, "assistant", "".join(_full_response))
                except Exception as _se:
                    logger.warning("[세션] 응답 저장 실패: %s", _se)
            _log_step(_t0, "전체완료", f"총 {time.time()-_t0:.1f}s 소요")
            yield _sse("done", {"session_id": _session_id})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/chat", response_model=NoriResponse)
async def chat(req: ChatRequest, llm: LlmDep, emb: EmbeddingDep):
    """자유 대화 — Java 개발 관련 질문/답변"""
    effective_history = _trim_history_if_context_shifted(req.message, req.history or [])
    resolved_msg, pr_meta = _resolve_pronouns_in_message(req.message, effective_history)
    effective_message = resolved_msg if (pr_meta.get('replaced') or pr_meta.get('subject_inferred')) else req.message
    rag_context = ""
    search_depth = "none"

    # 프로젝트 프로필 컨텍스트 (서버 저장)
    project_context = req.project_context
    if req.project_id:
        from app.config.settings import get_settings
        from app.service import profile_store
        settings = get_settings()
        content = profile_store.load_profile(settings, req.user_id or "default", req.project_id)
        if content:
            project_context = (project_context + "\n\n[프로젝트 프로필]\n" + content[:12000]) if project_context else content[:12000]

    if req.use_rag and emb.is_available:
        results = await emb.search(query=effective_message, top_k=5)
        if req.project_id and req.user_id:
            try:
                pr = await emb.search_profiles(effective_message, req.user_id, req.project_id, top_k=5)
                if pr:
                    results = pr + (results or [])
                    results = results[:8]
            except Exception:
                pass
        if results:
            rag_context = "\n---\n".join(
                f"[{r['collection']}] {r['text'][:600]}"
                for r in results
            )
            search_depth = "chroma"

    answer = await llm.chat(
        question=effective_message,
        history=effective_history,
        rag_context=rag_context,
        project_context=project_context,
    )
    return NoriResponse(
        data={"answer": answer},
        search_depth=search_depth,
    )


@router.post("/profile/summarize", response_model=NoriResponse)
async def summarize_profile(req: ProfileSummarizeRequest, llm: LlmDep):
    """프로젝트 프로필을 분석하여 비즈니스 기능 요약 생성"""
    summary = await llm.summarize_profile(req.profile_content)
    return NoriResponse(data={"summary": summary})


@router.post("/profile/describe-file", response_model=NoriResponse)
async def describe_file(req: DescribeFileRequest, llm: LlmDep):
    """소스코드를 AI로 분석하여 1~2줄 비즈니스 설명 생성"""
    code_len = len(req.code) if req.code else 0
    code_lines = req.code.count('\n') + 1 if req.code else 0
    truncated = ""
    if req.code:
        _end = req.code.rstrip()
        _ok_endings = ('}', ';', '%>', '-->', '>', '*/', ')', ']', '"', "'")
        if not any(_end.endswith(e) for e in _ok_endings):
            truncated = "⚠절삭"
    logger.info("[describe-file] %s — %d자, %d줄 %s", req.filename, code_len, code_lines, truncated)
    # 파일 로그도 남김
    import os
    _log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "describe-file.log")
    with open(_log_path, "a", encoding="utf-8") as _lf:
        _last5 = req.code.rstrip()[-80:] if req.code and len(req.code) > 80 else (req.code or "")
        _lf.write(f"[{req.filename}] {code_len}자, {code_lines}줄 {truncated} | 끝: {repr(_last5)}\n")
    description = await llm.describe_file(req.code, req.filename)
    return NoriResponse(data={"description": description})


@router.post("/embedding/add", response_model=NoriResponse)
async def add_embedding(req: EmbeddingAddRequest, emb: EmbeddingDep):
    """커스텀 문서를 벡터 DB에 추가"""
    ids = [f"{req.collection}-custom-{i}" for i in range(len(req.documents))]
    metadatas = req.metadatas if req.metadatas else [{}] * len(req.documents)

    await emb.add_documents(
        collection_name=req.collection,
        documents=req.documents,
        metadatas=metadatas,
        ids=ids,
    )
    return NoriResponse(data={"added": len(req.documents), "collection": req.collection})


@router.get("/embedding/stats", response_model=NoriResponse)
async def embedding_stats(emb: EmbeddingDep):
    """임베딩 통계 — 저장 문서 수, 컬렉션별 현황"""
    stats = await emb.get_stats()
    return NoriResponse(data=stats)


# ── WebSocket 스트리밍 대화 ──
ws_router = APIRouter()


@ws_router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    """WebSocket 스트리밍 대화"""
    await ws.accept()

    try:
        llm = get_llm_service()
        emb = get_embedding_service()
    except Exception:
        await ws.close(code=1011, reason="서비스 초기화 실패")
        return

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            message = data.get("message", "")
            history = data.get("history", [])
            use_rag = data.get("use_rag", True)

            rag_context = ""
            if use_rag and emb.is_available:
                results = await emb.search(query=message, top_k=5)
                if results:
                    rag_context = "\n---\n".join(
                        r["text"][:600] for r in results
                    )

            async for token in llm.stream_chat(
                question=message,
                history=history,
                rag_context=rag_context,
            ):
                await ws.send_json({"type": "token", "content": token})

            await ws.send_json({"type": "done"})

    except WebSocketDisconnect:
        logger.info("WebSocket 클라이언트 연결 해제")
    except json.JSONDecodeError:
        await ws.close(code=1003, reason="잘못된 JSON 형식")
    except Exception as e:
        logger.error(f"WebSocket 에러: {e}")
        await ws.close(code=1011, reason=str(e))
