"""
Nori AI 서버 테스트 공통 픽스처
- Mock LLM / Embedding 서비스
- FastAPI TestClient
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.service.llm_service import LlmService
from app.service.embedding_service import EmbeddingService
from app.service.memo_service import MemoService
from app.api import deps


# ── 테스트용 Settings ──
@pytest.fixture
def test_settings():
    """API 키 비활성 상태의 테스트 설정"""
    return Settings(
        ai_provider="openai",
        ai_api_key="test-key-not-real",
        ai_model="gpt-4o-mini",
        api_key="",  # 인증 비활성
        chroma_persist_dir="./test_chroma_tmp",
    )


# ── Mock LLM Provider ──
@pytest.fixture
def mock_llm_service(test_settings):
    """LLM 호출을 모킹한 서비스 — 실제 API 호출 없음"""
    service = MagicMock(spec=LlmService)

    # 모든 async 메서드를 AsyncMock으로
    service.complete_code = AsyncMock(return_value="// 자동완성 코드\nSystem.out.println(\"Hello\");")
    service.generate_code = AsyncMock(return_value="public class Sample {\n    // 생성된 코드\n}")
    service.explain_code = AsyncMock(return_value="이 코드는 ArrayList를 생성하고 요소를 추가합니다.")
    service.explain_class = AsyncMock(return_value="이 클래스는 Service 레이어를 담당합니다.")
    service.analyze_error = AsyncMock(return_value="NullPointerException은 null 참조 접근 시 발생합니다. list 초기화가 필요합니다.")
    service.fix_error = AsyncMock(return_value="// 수정된 코드\nList<String> list = new ArrayList<>();")
    service.review_code = AsyncMock(return_value="🟡 잠재적 NPE: line 5에서 null 체크 필요")
    service.refactor_code = AsyncMock(return_value="// 리팩토링 완료\npublic void process() { ... }")
    service.generate_test = AsyncMock(return_value="@Test\nvoid testAdd() { ... }")
    service.generate_javadoc = AsyncMock(return_value="/** 사용자 관리 서비스 */")
    service.suggest_debug = AsyncMock(return_value="1. UserService.findById() 메서드에 브레이크포인트 설정")
    service.chat = AsyncMock(return_value="ArrayList은 가변 크기 배열 구현체입니다.")

    service.classify_and_route = AsyncMock(
        return_value='{"tasks":[{"intent":"QUESTION","detail":"질문","files":[]}]}'
    )

    service.summarize_profile = AsyncMock(return_value="## 🔍 프로젝트 기능 요약\n테스트 프로젝트")

    service.pick_files = AsyncMock(return_value="src/Main.java\nsrc/App.java")

    async def _stream_chat(**kwargs):
        for token in ["Array", "List", "은 ", "리스트 ", "구현체"]:
            yield token

    service.stream_chat = _stream_chat

    async def _stream_task(*args, **kwargs):
        for token in ["안녕", "하세요", "!  ", "답변"]:
            yield token

    service.stream_task = _stream_task

    return service


# ── Mock Embedding Service ──
@pytest.fixture
def mock_embedding_service():
    """ChromaDB 호출을 모킹한 임베딩 서비스"""
    service = MagicMock(spec=EmbeddingService)
    service.is_available = True

    service.search = AsyncMock(return_value=[
        {
            "text": "[Java API] java.util.ArrayList\npublic class ArrayList<E>",
            "metadata": {"source_type": "javadoc", "class_name": "ArrayList", "package_name": "java.util"},
            "collection": "javadoc",
            "score": 0.85,
            "id": "javadoc:java.util.ArrayList:overview",
        }
    ])

    service.search_by_error = AsyncMock(return_value=[
        {
            "text": "NullPointerException 해결 사례",
            "metadata": {"source_type": "community"},
            "collection": "community",
            "score": 0.78,
            "id": "community:npe-fix",
        }
    ])

    service.search_by_version = AsyncMock(return_value=[])

    service.add_documents = AsyncMock()

    service.get_stats = AsyncMock(return_value={
        "status": "connected",
        "collections": {
            "javadoc": 8432, "spring": 13091, "egov": 0,
            "community": 0, "web-ui": 11406, "desktop-ui": 7589,
            "errors": 0, "custom": 0,
        },
        "total_documents": 40518,
    })

    service.initialize = AsyncMock()

    return service


# ── Mock Memo Service ──
@pytest.fixture
def mock_memo_service(tmp_path):
    """임시 디렉토리를 사용하는 MemoService"""
    return MemoService(data_dir=str(tmp_path / "memos"))


# ── TestClient ──
@pytest.fixture
def client(mock_llm_service, mock_embedding_service, mock_memo_service, test_settings):
    """Mock 서비스가 주입된 FastAPI TestClient"""
    from app.main import app

    # dependency_overrides — lifespan이 실제 서비스를 생성해도 DI 레벨에서 Mock 주입
    app.dependency_overrides[deps.get_settings] = lambda: test_settings
    app.dependency_overrides[deps.get_llm_service] = lambda: mock_llm_service
    app.dependency_overrides[deps.get_embedding_service] = lambda: mock_embedding_service
    app.dependency_overrides[deps.get_memo_service_dep] = lambda: mock_memo_service

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
