"""
API 요청/응답 DTO 모델
Pydantic v2 기반
"""
from pydantic import BaseModel, Field


# ── 공통 응답 ──
class NoriResponse(BaseModel):
    success: bool = True
    data: dict | list | str | None = None
    error: str | None = None
    search_depth: str | None = None  # chroma | milvus | both


# ── 시스템 ──
class HealthResponse(BaseModel):
    status: str
    server: str = "nori-ai"
    version: str = "0.1.0"
    ai_provider: str
    ai_model: str
    vector_db: dict
    uptime_seconds: float


class ConfigUpdateRequest(BaseModel):
    ai_model: str | None = None
    ai_temperature: float | None = None
    ai_max_tokens: int | None = None


# ── 코드 작성 ──
class CompletionRequest(BaseModel):
    code: str = Field(..., description="현재 에디터의 코드")
    cursor_context: str = Field("", description="커서 주변 컨텍스트")
    language: str = Field("java", description="프로그래밍 언어")
    file_path: str = Field("", description="파일 경로")
    project_type: str = Field("", description="프로젝트 타입 (spring-boot, egov 등)")


class GenerateRequest(BaseModel):
    description: str = Field(..., description="생성할 코드 설명")
    language: str = Field("java", description="프로그래밍 언어")
    project_context: str = Field("", description="프로젝트 컨텍스트")
    project_type: str = Field("", description="프로젝트 타입")
    java_version: str = Field("", description="Java 버전")


class RefactorRequest(BaseModel):
    code: str = Field(..., description="리팩토링 대상 코드")
    instruction: str = Field("", description="리팩토링 지시사항")
    language: str = Field("java", description="프로그래밍 언어")


class TestGenerateRequest(BaseModel):
    code: str = Field(..., description="테스트 대상 코드")
    language: str = Field("java", description="프로그래밍 언어")
    test_framework: str = Field("junit5", description="테스트 프레임워크")


# ── 코드 분석 ──
class ExplainRequest(BaseModel):
    code: str = Field(..., description="설명할 코드")
    language: str = Field("java", description="프로그래밍 언어")


class ClassExplainRequest(BaseModel):
    code: str = Field(..., description="클래스 코드")
    class_info: str = Field("", description="추가 정보 (상속관계 등)")


class ReviewRequest(BaseModel):
    code: str = Field(..., description="리뷰할 코드")
    language: str = Field("java", description="프로그래밍 언어")


class DocGenerateRequest(BaseModel):
    code: str = Field(..., description="JavaDoc 생성 대상 코드")


# ── 에러/디버깅 ──
class ErrorAnalyzeRequest(BaseModel):
    error_message: str = Field(..., description="에러 메시지")
    code: str = Field("", description="관련 코드")
    stacktrace: str = Field("", description="스택트레이스")
    java_version: str = Field("", description="Java 버전")
    framework: str = Field("", description="프레임워크 (spring-boot, egov 등)")


class ErrorFixRequest(BaseModel):
    error_message: str = Field(..., description="에러 메시지")
    code: str = Field(..., description="수정 대상 코드")


class DebugSuggestRequest(BaseModel):
    error_message: str = Field(..., description="에러 메시지")
    code: str = Field("", description="관련 코드")


# ── 검색/대화 ──
class SearchRequest(BaseModel):
    query: str = Field(..., description="검색 쿼리")
    top_k: int = Field(5, description="결과 수")
    collections: list[str] | None = Field(None, description="검색 대상 컬렉션")
    java_version: str = Field("", description="Java 버전 필터")


class ChatRequest(BaseModel):
    message: str = Field(..., description="사용자 메시지")
    history: list[dict] = Field(default_factory=list, description="대화 이력")
    use_rag: bool = Field(True, description="RAG 검색 사용 여부")
    project_context: str = Field("", description="프로젝트 소스 코드 컨텍스트 (파일 트리 + 주요 소스)")
    project_id: str = Field("", description="서버 저장 프로젝트 ID — profile 기반 컨텍스트")
    user_id: str = Field("default", description="사용자 ID")


class PickFilesRequest(BaseModel):
    question: str = Field(..., description="사용자 질문")
    profile: str = Field(..., description=".nori-profile.md 내용")


class SmartChatRequest(BaseModel):
    message: str = Field(..., description="사용자 메시지")
    profile: str = Field("", description=".nori-profile.md 내용 (또는 project_id로 서버에서 로드)")
    file_contents: dict = Field(default_factory=dict, description="파일경로: 내용 맵")
    history: list[dict] = Field(default_factory=list, description="대화 이력")
    use_rag: bool = Field(True, description="RAG 검색 사용 여부")
    use_efficient: bool = Field(False, description="효율 모드: LLM 1회만 사용 (키워드→검색→1회추론)")
    selected_code: str = Field("", description="에디터에서 선택된 코드")
    tasks: list = Field(default_factory=list, description="사전 분류된 태스크 목록 (2차 호출 시)")
    project_id: str = Field("", description="서버 저장 프로젝트 ID — 있으면 profile 대신 서버 프로필 사용")
    user_id: str = Field("default", description="사용자 ID (프로젝트 분리용)")
    session_id: str = Field("", description="채팅 세션 ID — 비어있으면 자동 생성")


class ChatMessage(BaseModel):
    role: str
    content: str


# ── 임베딩 ──
class EmbeddingAddRequest(BaseModel):
    collection: str = Field(..., description="컬렉션 이름")
    documents: list[str] = Field(..., description="추가할 문서 내용")
    metadatas: list[dict] = Field(default_factory=list, description="메타데이터")


# ── 프로젝트 컨텍스트 ──
class ProjectAnalyzeRequest(BaseModel):
    project_root: str = Field("", description="프로젝트 루트 경로")
    pom_xml: str = Field("", description="pom.xml 내용")
    build_gradle: str = Field("", description="build.gradle 내용")
    file_list: list[str] = Field(default_factory=list, description="프로젝트 파일 목록")


class DependencyAnalyzeRequest(BaseModel):
    pom_xml: str = Field("", description="pom.xml 내용")
    build_gradle: str = Field("", description="build.gradle 내용")


class LibrarySuggestRequest(BaseModel):
    description: str = Field(..., description="필요한 기능 설명")
    project_type: str = Field("", description="프로젝트 타입")


class ProfileSummarizeRequest(BaseModel):
    profile_content: str = Field(..., description=".nori-profile.md 원본 내용")

class DescribeFileRequest(BaseModel):
    code: str = Field(..., description="소스코드 내용")
    filename: str = Field("", description="파일명")

class ProjectScanRequest(BaseModel):
    project_name: str = Field("", description="프로젝트 이름")
    pom_xml: str = Field("", description="pom.xml 내용")
    build_gradle: str = Field("", description="build.gradle 내용")
    web_xml: str = Field("", description="web.xml 내용")
    application_properties: str = Field("", description="application.properties/yml 내용")
    file_tree: str = Field("", description="프로젝트 파일 트리")
    file_list: list[str] = Field(default_factory=list, description="프로젝트 파일 목록")
