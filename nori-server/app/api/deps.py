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
from app.db import connection as db_conn
# ── 서비스 싱글턴 ──
_llm_service: LlmService | None = None
_embedding_service: EmbeddingService | None = None
_memo_service: MemoService | None = None
_start_time: float = time.time()


def get_start_time() -> float:
    return _start_time


async def init_services(settings: Settings):
    """서버 시작 시 서비스 초기화"""
    global _llm_service, _embedding_service, _memo_service, _start_time
    _start_time = time.time()
    _llm_service = LlmService(settings)
    _embedding_service = EmbeddingService(settings)
    _memo_service = MemoService()
    await _embedding_service.initialize()
    # LLM 서비스에 메모 서비스 연결
    _llm_service.set_memo_service(_memo_service)
    # MariaDB 풀 초기화
    await db_conn.init_db_pool(settings)


async def close_services():
    """서버 종료 시 서비스 정리"""
    await db_conn.close_db_pool()


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


# 타입 별칭
LlmDep = Annotated[LlmService, Depends(get_llm_service)]
EmbeddingDep = Annotated[EmbeddingService, Depends(get_embedding_service)]
MemoDep = Annotated[MemoService, Depends(get_memo_service_dep)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
