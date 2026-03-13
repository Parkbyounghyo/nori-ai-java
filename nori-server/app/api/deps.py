"""
FastAPI 의존성 주입 — 서비스 인스턴스 제공 + API 키 인증
"""
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config.settings import Settings, get_settings
from app.service.llm_service import LlmService
from app.service.embedding_service import EmbeddingService
from app.service.memo_service import MemoService
from app.service.pl_workflow_service import PlWorkflowService

# ── 서비스 싱글톤 ──
_llm_service: LlmService | None = None
_embedding_service: EmbeddingService | None = None
_memo_service: MemoService | None = None
_pl_workflow_service: PlWorkflowService | None = None
_start_time: float = time.time()


def get_start_time() -> float:
    return _start_time


async def init_services(settings: Settings):
    """서버 시작 시 서비스 초기화"""
    global _llm_service, _embedding_service, _memo_service, _pl_workflow_service, _start_time
    _start_time = time.time()
    _llm_service = LlmService(settings)
    _embedding_service = EmbeddingService(settings)
    _memo_service = MemoService()
    _pl_workflow_service = PlWorkflowService()
    _pl_workflow_service.set_llm_service(_llm_service)
    await _embedding_service.initialize()
    # LLM 서비스에 메모 서비스 연결
    _llm_service.set_memo_service(_memo_service)


def get_llm_service() -> LlmService:
    if _llm_service is None:
        raise HTTPException(status_code=503, detail="LLM 서비스가 초기화되지 않았습니다")
    return _llm_service


def get_embedding_service() -> EmbeddingService:
    if _embedding_service is None:
        raise HTTPException(status_code=503, detail="임베딩 서비스가 초기화되지 않았습니다")
    return _embedding_service


async def verify_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
):
    """API 키 인증 (설정에 키가 있을 때만 활성화)"""
    if settings.api_key and settings.api_key != x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 API 키입니다",
        )


def get_memo_service_dep() -> MemoService:
    global _memo_service
    if _memo_service is None:
        _memo_service = MemoService()
    return _memo_service


def get_pl_workflow_service() -> PlWorkflowService:
    global _pl_workflow_service
    if _pl_workflow_service is None:
        _pl_workflow_service = PlWorkflowService()
    return _pl_workflow_service


# 타입 별칭
LlmDep = Annotated[LlmService, Depends(get_llm_service)]
EmbeddingDep = Annotated[EmbeddingService, Depends(get_embedding_service)]
MemoDep = Annotated[MemoService, Depends(get_memo_service_dep)]
PlDep = Annotated[PlWorkflowService, Depends(get_pl_workflow_service)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
