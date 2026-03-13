"""
채팅 세션 관리 API — 새 채팅, 대화 목록, 이력 조회/저장/삭제
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import verify_api_key
from app.api.models import NoriResponse
from app.service import chat_session_service as css

logger = logging.getLogger("nori-server")

router = APIRouter(
    prefix="/api/v1/sessions",
    tags=["chat-sessions"],
    dependencies=[Depends(verify_api_key)],
)


# ── 요청 모델 ──
class CreateSessionRequest(BaseModel):
    user_id: str = Field("default", description="사용자 ID")
    title: str = Field("", description="세션 제목 (비워두면 자동 생성)")


class SaveMessagesRequest(BaseModel):
    user_id: str = Field("default")
    session_id: str = Field(..., description="세션 ID")
    messages: list[dict] = Field(..., description="전체 메시지 배열 [{role, content}]")


class AppendMessageRequest(BaseModel):
    user_id: str = Field("default")
    session_id: str = Field(..., description="세션 ID")
    role: str = Field(..., description="user | assistant")
    content: str = Field(..., description="메시지 내용")


class RenameSessionRequest(BaseModel):
    user_id: str = Field("default")
    session_id: str = Field(..., description="세션 ID")
    title: str = Field(..., description="새 제목")


# ── 엔드포인트 ──

@router.post("/create", response_model=NoriResponse)
async def create_session(req: CreateSessionRequest):
    """새 채팅 세션 생성"""
    session = css.create_session(req.user_id, req.title)
    return NoriResponse(data=session)


@router.get("/list", response_model=NoriResponse)
async def list_sessions(user_id: str = "default", limit: int = 50):
    """채팅 세션 목록 조회 (최신순)"""
    sessions = css.list_sessions(user_id, limit)
    return NoriResponse(data=sessions)


@router.get("/{session_id}", response_model=NoriResponse)
async def get_session(session_id: str, user_id: str = "default"):
    """특정 세션의 전체 데이터 (메시지 포함) 조회"""
    session = css.get_session(user_id, session_id)
    if not session:
        return NoriResponse(success=False, error="세션을 찾을 수 없습니다")
    return NoriResponse(data=session)


@router.post("/save", response_model=NoriResponse)
async def save_messages(req: SaveMessagesRequest):
    """세션에 메시지 전체 저장"""
    result = css.save_messages(req.user_id, req.session_id, req.messages)
    return NoriResponse(data=result)


@router.post("/append", response_model=NoriResponse)
async def append_message(req: AppendMessageRequest):
    """세션에 메시지 1개 추가"""
    result = css.append_message(req.user_id, req.session_id, req.role, req.content)
    return NoriResponse(data=result)


@router.post("/rename", response_model=NoriResponse)
async def rename_session(req: RenameSessionRequest):
    """세션 제목 변경"""
    result = css.rename_session(req.user_id, req.session_id, req.title)
    if not result:
        return NoriResponse(success=False, error="세션을 찾을 수 없습니다")
    return NoriResponse(data=result)


@router.delete("/{session_id}", response_model=NoriResponse)
async def delete_session(session_id: str, user_id: str = "default"):
    """세션 삭제"""
    ok = css.delete_session(user_id, session_id)
    if not ok:
        return NoriResponse(success=False, error="세션을 찾을 수 없습니다")
    return NoriResponse(data={"deleted": session_id})


@router.get("/{session_id}/qa-pairs", response_model=NoriResponse)
async def list_qa_pairs(session_id: str, user_id: str = "default"):
    """세션의 Q&A 쌍 목록 — 프론트 체크박스 렌더링용

    반환: [{"index": 0, "question": "첫 질문...", "has_answer": true}, ...]
    """
    pairs = css.list_qa_pairs(user_id, session_id)
    return NoriResponse(data=pairs)
