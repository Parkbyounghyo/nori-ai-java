"""
쿼리 확장기 — Java/Spring 도메인 사전 기반

단일 키워드를 관련 용어/어노테이션/클래스명으로 확장하여
벡터 검색 커버리지를 높인다.

사용 예:
    queries = expand_query("스프링 트랜잭션 설정")
    # → ["스프링 트랜잭션 설정", "스프링 트랜잭션 설정 @Transactional TransactionManager", ...]
"""
import re

# ── 도메인 동의어 사전 ──
# key: 검색어(한글/영문), value: 관련 용어 목록 (중요도 순)
_EXPANSION_DICT: dict[str, list[str]] = {
    # 트랜잭션
    "트랜잭션": ["@Transactional", "TransactionManager", "transaction", "@EnableTransactionManagement"],
    "transaction": ["@Transactional", "TransactionManager", "트랜잭션", "rollback"],
    # 의존성 주입
    "의존성주입": ["@Autowired", "@Inject", "dependency injection", "IoC"],
    "autowired": ["@Autowired", "의존성 주입", "@Inject", "DI"],
    # 보안/인증
    "로그인": ["login", "LoginService", "AuthService", "authentication", "세션"],
    "인증": ["authentication", "AuthService", "Spring Security", "로그인", "@PreAuthorize"],
    "권한": ["authorization", "role", "ROLE_", "@PreAuthorize", "@Secured"],
    "보안": ["security", "Spring Security", "SecurityConfig", "CSRF", "@EnableWebSecurity"],
    "security": ["Spring Security", "보안", "@EnableWebSecurity", "SecurityConfig"],
    # 세션
    "세션": ["session", "HttpSession", "@SessionAttribute", "SessionStorage"],
    "session": ["세션", "HttpSession", "SessionAttribute"],
    # 페이징
    "페이징": ["pagination", "Pageable", "PageHelper", "paging", "rownum"],
    "paging": ["페이징", "PageHelper", "Pageable", "rownum"],
    # 파일 업로드
    "파일업로드": ["multipart", "MultipartFile", "fileUpload", "@RequestPart"],
    "파일": ["file", "MultipartFile", "File", "Path", "Resource"],
    # 예외/에러
    "예외": ["exception", "Exception", "@ExceptionHandler", "try-catch", "throws"],
    "에러": ["error", "exception", "Exception", "@ExceptionHandler", "ExceptionAdvice"],
    "exception": ["예외", "@ExceptionHandler", "throws", "catch", "RuntimeException"],
    # 배치/스케줄
    "배치": ["batch", "Job", "Step", "ItemReader", "ItemWriter", "@Scheduled"],
    "스케줄": ["@Scheduled", "Scheduler", "cron", "schedule", "@EnableScheduling"],
    # REST API
    "API": ["@RestController", "@GetMapping", "@PostMapping", "RequestMapping", "Endpoint"],
    "REST": ["@RestController", "@GetMapping", "@PostMapping", "@PutMapping", "@DeleteMapping"],
    "컨트롤러": ["Controller", "@Controller", "@RestController", "RequestMapping"],
    # 요청/응답
    "응답": ["response", "ResponseEntity", "HttpServletResponse", "@ResponseBody"],
    "요청": ["request", "HttpServletRequest", "@RequestBody", "@RequestParam", "@PathVariable"],
    # CRUD 동작
    "조회": ["select", "findBy", "get", "query", "selectList", "selectOne"],
    "등록": ["insert", "save", "add", "create", "Mapper.insert"],
    "수정": ["update", "modify", "edit", "Mapper.update"],
    "삭제": ["delete", "remove", "Mapper.delete"],
    "검색": ["search", "findAll", "query", "keyword", "like"],
    "목록": ["list", "findAll", "selectList", "getList"],
    "상세": ["detail", "findById", "selectOne", "getDetail"],
    # 계층
    "서비스": ["Service", "@Service", "ServiceImpl", "business logic"],
    "매퍼": ["Mapper", "@Mapper", "MyBatis", "resultMap", "dao"],
    "dao": ["Mapper", "@Mapper", "DAO", "MyBatis", "JPA Repository"],
    # ORM/데이터
    "mybatis": ["mapper", "Mapper", "resultMap", "parameterType", "namespace", "xml"],
    "JPA": ["@Entity", "@Repository", "findById", "JpaRepository", "JPQL", "@Column"],
    "mapper": ["Mapper", "@Mapper", "MyBatis", "xml", "SQL"],
    # 설정
    "설정": ["config", "application.properties", "yml", "@Configuration", "@Bean"],
    "config": ["설정", "application.properties", "yml", "@Configuration", "@Bean"],
    # 캐시
    "캐시": ["cache", "@Cacheable", "@CacheEvict", "Redis", "EhCache"],
    "cache": ["캐시", "@Cacheable", "Redis", "@EnableCaching"],
    # 테스트
    "테스트": ["test", "@Test", "MockMvc", "Mockito", "@SpringBootTest"],
    # 로깅
    "로그": ["logging", "log", "Logger", "@Slf4j", "logback"],
    "logging": ["로그", "Logger", "@Slf4j", "logback", "log4j"],
    # 전자정부 특화
    "전자정부": ["egov", "eGovFrame", "EgovAbstractServiceImpl", "EgovAbstractDAO"],
    "egov": ["전자정부", "eGovFrame", "EgovAbstractServiceImpl", "EgovAbstractDAO"],
    "공통코드": ["code", "CodeService", "comCode", "common code", "EgovCom"],
}

_CAMEL_SPLIT_RE = re.compile(r"([A-Z][a-z]+)")


def expand_query(query: str, max_expansions: int = 3) -> list[str]:
    """쿼리를 도메인 사전 기반으로 확장. 원본 포함 최대 max_expansions+1개 반환.

    Args:
        query: 원본 검색 쿼리
        max_expansions: 추가 확장 쿼리 수 (원본 제외)

    Returns:
        [원본 쿼리, 확장 쿼리1, 확장 쿼리2, ...]
    """
    extra_terms: list[str] = []
    seen: set[str] = {query.lower()}

    # 토큰 분리 (한글 단어 + 영어 단어 + 어노테이션)
    tokens = re.findall(r"[\uAC00-\uD7A3]+|[@$]?[a-zA-Z][a-zA-Z0-9_.]*", query)

    for tok in tokens:
        normalized = tok.strip("@.$").lower()
        for key, synonyms in _EXPANSION_DICT.items():
            key_norm = key.lower().strip("@.$")
            if key_norm == normalized or normalized in key_norm or key_norm in normalized:
                for syn in synonyms:
                    if syn.lower() not in seen:
                        extra_terms.append(syn)
                        seen.add(syn.lower())
                break

    # 확장 쿼리 생성: 원본 + 확장 텀 2~3개씩 묶어서 append
    expanded: list[str] = [query]
    chunk_size = 2
    for i in range(0, len(extra_terms), chunk_size):
        chunk = " ".join(extra_terms[i : i + chunk_size])
        expanded.append(f"{query} {chunk}")
        if len(expanded) - 1 >= max_expansions:
            break

    return expanded


def expand_queries_multi(queries: list[str], max_per_query: int = 2) -> list[str]:
    """여러 쿼리를 각각 확장 후 중복 제거하여 반환."""
    result: list[str] = []
    seen: set[str] = set()
    for q in queries:
        for eq in expand_query(q, max_per_query):
            if eq not in seen:
                result.append(eq)
                seen.add(eq)
    return result
