"""
PL 워크플로우 API — 파일 분석 → TODO 관리 → 소스 제안 → 피드백 → 보고서
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import verify_api_key, get_pl_workflow_service
from app.api.models import NoriResponse
from app.service.pl_workflow_service import PlWorkflowService

from typing import Annotated

logger = logging.getLogger("nori-server")

PlDep = Annotated[PlWorkflowService, Depends(get_pl_workflow_service)]

router = APIRouter(
    prefix="/api/v1/pl",
    tags=["pl-workflow"],
    dependencies=[Depends(verify_api_key)],
)


# ═══════════════════════════════════════
# DTO
# ═══════════════════════════════════════

class AnalyzeRequest(BaseModel):
    user_request: str = Field(..., description="사용자 수정 요청")
    project_profile: str = Field("", description=".nori-profile.md 내용")
    file_tree: str = Field("", description="프로젝트 파일 트리")
    file_contents: dict[str, str] = Field(default_factory=dict, description="관련 파일 경로:내용 맵")
    dependency_map: dict[str, dict] = Field(default_factory=dict, description="도메인별 의존관계 맵")


class SuggestSourceRequest(BaseModel):
    todo_id: str = Field(..., description="TODO ID")
    order: int = Field(..., description="TODO 항목 순서 번호")
    file_content: str = Field(..., description="현재 파일의 전체 내용")


class RetrySourceRequest(BaseModel):
    todo_id: str = Field(..., description="TODO ID")
    order: int = Field(..., description="TODO 항목 순서 번호")
    file_content: str = Field(..., description="현재 파일의 전체 내용")
    reason: str = Field("", description="재생성 사유")


class UpdateTodoItemRequest(BaseModel):
    todo_id: str = Field(..., description="TODO ID")
    order: int = Field(..., description="TODO 항목 순서 번호")
    status: str = Field("", description="상태 (done, skipped, failed)")
    test_status: str | None = Field(None, description="테스트 상태 (pass, fail, skipped)")
    test_reason: str | None = Field(None, description="테스트 사유")


class FeedbackRequest(BaseModel):
    feedback_type: str = Field(..., description="피드백 타입 (like, dislike)")
    file_name: str = Field(..., description="파일명")
    file_path: str = Field("", description="파일 경로")
    user_request: str = Field("", description="원본 요청")
    reason: str = Field("", description="사유 (안좋아요 시)")
    suggested_source: str = Field("", description="제안 소스 스냅샷")
    start_line: int | None = Field(None, description="시작 라인")
    todo_id: str = Field("", description="관련 TODO ID")


# ═══════════════════════════════════════
# Step 1. 파일 분석
# ═══════════════════════════════════════

@router.post("/analyze", response_model=NoriResponse)
async def analyze_request(req: AnalyzeRequest, pl: PlDep):
    """수정 요청을 분석하여 대상 파일 목록 + 순서 반환 (코드 수정 없음)"""
    result = await pl.analyze_request(
        user_request=req.user_request,
        project_profile=req.project_profile,
        file_tree=req.file_tree,
        file_contents=req.file_contents,
        dependency_map=req.dependency_map or None,
    )
    if "error" in result and not result.get("files"):
        return NoriResponse(success=False, error=result["error"], data=result)
    return NoriResponse(data=result)


@router.post("/analyze-and-create", response_model=NoriResponse)
async def analyze_and_create_todo(req: AnalyzeRequest, pl: PlDep):
    """분석 + TODO 자동 생성 (한번에 Step 1~2)"""
    analysis = await pl.analyze_request(
        user_request=req.user_request,
        project_profile=req.project_profile,
        file_tree=req.file_tree,
        file_contents=req.file_contents,
        dependency_map=req.dependency_map or None,
    )
    if "error" in analysis and not analysis.get("files"):
        return NoriResponse(success=False, error=analysis["error"], data=analysis)

    todo = pl.create_todo(req.user_request, analysis)
    return NoriResponse(data={"analysis": analysis, "todo": todo})


# ═══════════════════════════════════════
# Step 2. TODO 관리
# ═══════════════════════════════════════

@router.get("/todo", response_model=NoriResponse)
async def get_active_todo(pl: PlDep):
    """현재 진행 중인 TODO 조회"""
    todo = pl.get_active_todo()
    if not todo:
        return NoriResponse(success=False, error="진행 중인 TODO가 없습니다")
    return NoriResponse(data=todo)


@router.get("/todo/{todo_id}", response_model=NoriResponse)
async def get_todo(todo_id: str, pl: PlDep):
    """특정 TODO 조회"""
    todo = pl.get_todo(todo_id)
    if not todo:
        return NoriResponse(success=False, error="TODO를 찾을 수 없습니다")
    return NoriResponse(data=todo)


@router.get("/todos", response_model=NoriResponse)
async def list_todos(pl: PlDep):
    """전체 TODO 목록"""
    return NoriResponse(data=pl.list_todos())


@router.put("/todo/item", response_model=NoriResponse)
async def update_todo_item(req: UpdateTodoItemRequest, pl: PlDep):
    """TODO 항목 상태 업데이트 (완료, 건너뛰기, 실패)"""
    updates = {}
    if req.status:
        updates["status"] = req.status
    if req.test_status is not None:
        updates["test_status"] = req.test_status
    if req.test_reason is not None:
        updates["test_reason"] = req.test_reason

    todo = pl.update_todo_item(req.todo_id, req.order, updates)
    if not todo:
        return NoriResponse(success=False, error="TODO 항목을 찾을 수 없습니다")
    return NoriResponse(data=todo)


@router.delete("/todo/{todo_id}", response_model=NoriResponse)
async def delete_todo(todo_id: str, pl: PlDep):
    """TODO 삭제"""
    if pl.delete_todo(todo_id):
        return NoriResponse(data="삭제 완료")
    return NoriResponse(success=False, error="TODO를 찾을 수 없습니다")


# ═══════════════════════════════════════
# Step 3. 순차 소스 제안
# ═══════════════════════════════════════

@router.post("/suggest", response_model=NoriResponse)
async def suggest_source(req: SuggestSourceRequest, pl: PlDep):
    """TODO 항목에 대한 수정 소스 제안"""
    result = await pl.suggest_source(
        todo_id=req.todo_id,
        order=req.order,
        file_content=req.file_content,
    )
    if "error" in result:
        return NoriResponse(success=False, error=result["error"], data=result)
    return NoriResponse(data=result)


@router.post("/retry", response_model=NoriResponse)
async def retry_source(req: RetrySourceRequest, pl: PlDep):
    """소스 재생성 (🔄 다시 버튼)"""
    result = await pl.retry_source(
        todo_id=req.todo_id,
        order=req.order,
        file_content=req.file_content,
        reason=req.reason,
    )
    if "error" in result:
        return NoriResponse(success=False, error=result["error"], data=result)
    return NoriResponse(data=result)


# ═══════════════════════════════════════
# Step 4. 피드백
# ═══════════════════════════════════════

@router.post("/feedback", response_model=NoriResponse)
async def save_feedback(req: FeedbackRequest, pl: PlDep):
    """피드백 저장 (👍 좋아요 / 👎 안좋아요)"""
    entry = pl.save_feedback(
        feedback_type=req.feedback_type,
        file_name=req.file_name,
        file_path=req.file_path,
        user_request=req.user_request,
        reason=req.reason,
        suggested_source=req.suggested_source,
        start_line=req.start_line,
        todo_id=req.todo_id,
    )
    return NoriResponse(data=entry)


@router.get("/feedbacks", response_model=NoriResponse)
async def get_feedbacks(
    pl: PlDep,
    date: str = "",
    feedback_type: str = "",
):
    """피드백 조회 (날짜/타입 필터)"""
    return NoriResponse(data=pl.get_feedbacks(date, feedback_type))


# ═══════════════════════════════════════
# Step 5. 보고서 & 히스토리
# ═══════════════════════════════════════

@router.post("/report/{todo_id}", response_model=NoriResponse)
async def generate_report(todo_id: str, pl: PlDep):
    """TODO 완료 후 보고서 생성 + 히스토리 저장"""
    report = await pl.generate_report(todo_id)
    if "error" in report:
        return NoriResponse(success=False, error=report["error"])
    return NoriResponse(data=report)


@router.get("/history", response_model=NoriResponse)
async def get_history(pl: PlDep, limit: int = 20):
    """작업 히스토리 조회"""
    return NoriResponse(data=pl.get_history(limit))
