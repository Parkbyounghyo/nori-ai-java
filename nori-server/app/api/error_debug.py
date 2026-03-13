"""
에러/디버깅 API — 에러 분석, 자동 수정, 디버깅 가이드
"""
import logging

from fastapi import APIRouter, Depends

from app.api.deps import LlmDep, EmbeddingDep, verify_api_key
from app.api.models import (
    ErrorAnalyzeRequest, ErrorFixRequest, DebugSuggestRequest, NoriResponse,
)

logger = logging.getLogger("nori-server")
router = APIRouter(
    prefix="/api/v1",
    tags=["error-debug"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("/error/analyze", response_model=NoriResponse)
async def analyze_error(req: ErrorAnalyzeRequest, llm: LlmDep, emb: EmbeddingDep):
    """에러 분석 — 에러 메시지 + 스택트레이스 → 원인 + 해결 방법"""
    rag_context = ""
    search_depth = "none"

    if emb.is_available:
        # 에러 메시지로 관련 사례 검색
        search_query = req.error_message
        if req.stacktrace:
            # 스택트레이스에서 핵심 에러 라인 추출
            lines = req.stacktrace.strip().split("\n")
            search_query = lines[0] if lines else search_query

        results = await emb.search_by_error(
            error_pattern=search_query,
            top_k=5,
            java_version=req.java_version or None,
        )
        if results:
            rag_context = "\n---\n".join(
                f"[{r['metadata'].get('source_name', '')}] {r['text'][:600]}"
                for r in results
            )
            search_depth = "chroma"

    analysis = await llm.analyze_error(
        error_message=req.error_message,
        code=req.code,
        stacktrace=req.stacktrace,
        rag_context=rag_context,
    )
    return NoriResponse(
        data={
            "analysis": analysis,
            "error_message": req.error_message,
            "framework": req.framework,
        },
        search_depth=search_depth,
    )


@router.post("/error/fix", response_model=NoriResponse)
async def fix_error(req: ErrorFixRequest, llm: LlmDep, emb: EmbeddingDep):
    """자동 수정 — 에러 코드를 받아서 수정된 코드 반환"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search_by_error(
            error_pattern=req.error_message, top_k=3,
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    fixed_code = await llm.fix_error(
        error_message=req.error_message,
        code=req.code,
        rag_context=rag_context,
    )
    return NoriResponse(data={"fixed_code": fixed_code})


@router.post("/debug/suggest", response_model=NoriResponse)
async def suggest_debug(req: DebugSuggestRequest, llm: LlmDep, emb: EmbeddingDep):
    """디버깅 가이드 — 브레이크포인트 위치, 확인 사항 제안"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(query=req.error_message, top_k=3)
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    guide = await llm.suggest_debug(
        error_message=req.error_message,
        code=req.code,
        rag_context=rag_context,
    )
    return NoriResponse(data={"debug_guide": guide})
