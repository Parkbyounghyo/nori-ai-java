"""
규칙 기반 키워드 추출기 — LLM 없이 사용자 질문에서 검색 키워드 추출

예: "자바 파일 업로드 할 때 용량 제한 에러 좀 고쳐줘."
    → File Upload, Max Size, Exception, Multipart, maxFileSize
"""
import re
import logging

logger = logging.getLogger("nori-server")

# 한글/일상어 → 기술 키워드 매핑 (벡터 검색·소스 매칭용)
KR_TO_TECH = {
    # 파일 관련
    "파일": ["file", "upload", "download", "multipart"],
    "업로드": ["upload", "multipart", "MultipartFile", "file upload"],
    "다운로드": ["download", "file"],
    "첨부": ["attach", "attachment", "file"],
    "용량": ["size", "max", "limit", "maxFileSize", "max-file-size"],
    "제한": ["limit", "max", "size", "restrict"],
    "크기": ["size", "max", "byte"],
    # 에러 관련
    "에러": ["error", "exception", "exception"],
    "오류": ["error", "exception"],
    "고쳐": ["fix", "error", "exception"],
    "수정": ["fix", "modify", "update"],
    "해결": ["fix", "resolve"],
    # 설정 관련
    "설정": ["config", "properties", "application.properties", "setting"],
    "application": ["application.properties", "application.yml"],
    "프로퍼티": ["properties", "application.properties"],
    # 업무 도메인
    "게시판": ["board", "bbs"],
    "공지": ["notice"],
    "결제": ["pay", "payment", "order"],
    "회원": ["member", "cust", "user"],
    "상품": ["product", "goods"],
    "주문": ["order"],
    "포인트": ["point"],
    "쿠폰": ["coupon"],
    "장바구니": ["cart"],
    "배송": ["delivery"],
    "로그인": ["login", "auth"],
    "관리자": ["admin"],
    "메뉴": ["menu"],
    "권한": ["auth", "role"],
    "메일": ["mail", "email", "smtp"],
    "이벤트": ["event"],
    "통계": ["stat"],
    "댓글": ["comment", "reply"],
    "카테고리": ["category", "cate"],
    "검색": ["search", "find"],
    "리뷰": ["review"],
    "FAQ": ["faq"],
    "faq": ["faq"],
    "문의": ["inq", "faq"],
    "제휴": ["cooper"],
    "승인": ["approval"],
}

# 기술 용어 별칭 (추가 확장)
TECH_SYNONYMS = {
    "파일업로드": ["file upload", "multipart", "MultipartFile", "maxFileSize"],
    "용량제한": ["max file size", "max-request-size", "multipart.max-file-size", "MaxUploadSizeExceededException"],
    "용량에러": ["MaxUploadSizeExceededException", "max file size", "multipart"],
}

STOP_WORDS = frozenset({
    "the", "and", "for", "from", "with", "java", "xml", "jsp",
    "src", "main", "impl", "dao", "web", "resources",
    "추가", "수정", "변경", "삭제", "해줘", "부탁", "있게", "없게",
    "할", "때", "같이", "같은", "이", "그", "저",
})


_profile_kr_to_tech_cache: dict = {}

def enrich_kr_to_tech_from_profile(profile: str):
    """프로필의 기능 블록에서 한글→영문 키워드를 동적 추출하여 KR_TO_TECH에 보강"""
    if not profile:
        return
    p_hash = str(hash(profile[:200]))
    if p_hash in _profile_kr_to_tech_cache:
        return
    _profile_kr_to_tech_cache[p_hash] = True
    blocks = re.split(r'####\s*\d+\.', profile)
    for block in blocks:
        if not block.strip():
            continue
        first_line = block.strip().split("\n")[0].strip()
        kr_words = re.findall(r'[가-힣]{2,}', first_line)
        en_words = set()
        for line in block.split("\n"):
            if "경로:" in line:
                fp = line.split(":", 1)[1].strip().split("|")[0].strip()
                fn = fp.rsplit("/", 1)[-1] if "/" in fp else fp
                words = re.findall(r'[a-z]{3,}', fn.lower().rsplit('.', 1)[0])
                en_words.update(w for w in words
                    if w not in ('controller', 'service', 'serviceimpl', 'impl', 'dao', 'list', 'view', 'write', 'update', 'regist', 'proc', 'mapper', 'sql', 'mybatis', 'admin'))
        if kr_words and en_words:
            for kw in kr_words:
                if kw not in KR_TO_TECH:
                    KR_TO_TECH[kw] = list(en_words)[:5]


def extract_keywords(question: str, profile: str = None) -> list[str]:
    """사용자 질문에서 검색용 키워드 추출 (규칙 기반, LLM 미사용)
    profile이 있으면 프로젝트별 키워드 자동 보강

    Returns:
        벡터 DB 검색 쿼리로 사용할 키워드 리스트
    """
    if not question or not question.strip():
        return []

    if profile:
        enrich_kr_to_tech_from_profile(profile)

    q = question.strip()
    keywords = set()

    # 1) 영문 단어 추출 (CamelCase 분리)
    for w in re.findall(r"[A-Za-z][a-zA-Z0-9]*", q):
        wl = w.lower()
        if len(wl) >= 2 and wl not in STOP_WORDS:
            keywords.add(wl)
        for sub in re.findall(r"[A-Z][a-z]+|[a-z]+", w):
            sl = sub.lower()
            if len(sl) >= 2 and sl not in STOP_WORDS:
                keywords.add(sl)

    # 2) 한글 → 기술 키워드 매핑 (기본 + 프로필 기반 동적 추가)
    for kr, tech_list in KR_TO_TECH.items():
        if kr in q:
            keywords.update(tech_list)

    # 3) 복합 한글 구문 (파일업로드, 용량제한 등)
    for phrase, tech_list in TECH_SYNONYMS.items():
        if phrase in q.replace(" ", ""):
            keywords.update(tech_list)

    # 4) 의미 있는 한글 명사 (2글자 이상)
    kr_words = re.findall(r"[가-힣]{2,}", q)
    for w in kr_words:
        if w in KR_TO_TECH:
            keywords.update(KR_TO_TECH[w])

    # 5) 에러/고쳐 관련 → Exception, fix 강화
    if any(k in q for k in ("에러", "오류", "고쳐", "해결", "수정")):
        keywords.update(["exception", "error", "fix"])

    result = [k for k in keywords if k and len(k) >= 2][:30]
    if result:
        logger.info("[키워드추출] 질문='%s' → %s", q[:60], result[:15])
    return result


def keywords_to_query(keywords: list[str]) -> str:
    """키워드 리스트를 벡터 DB 검색 쿼리 문자열로 변환"""
    if not keywords:
        return ""
    return " ".join(keywords[:15])
