"""
코드 작성 지원 API — 자동완성, 코드 생성, 리팩토링, 테스트 생성
"""
import logging

from fastapi import APIRouter, Depends

from app.api.deps import LlmDep, EmbeddingDep, verify_api_key
from app.api.models import (
    CompletionRequest, GenerateRequest, RefactorRequest,
    TestGenerateRequest, NoriResponse,
)

logger = logging.getLogger("nori-server")
router = APIRouter(
    prefix="/api/v1",
    tags=["code-writing"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("/completion", response_model=NoriResponse)
async def code_completion(req: CompletionRequest, llm: LlmDep, emb: EmbeddingDep):
    """코드 자동완성 — 커서 위치 기반 다음 코드 제안"""
    rag_context = ""
    search_depth = "none"

    if emb.is_available:
        results = await emb.search(
            query=req.cursor_context or req.code[-500:],
            top_k=3,
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)
            search_depth = "chroma"

    suggestion = await llm.complete_code(
        code=req.code,
        cursor_context=req.cursor_context,
        language=req.language,
        rag_context=rag_context,
    )
    return NoriResponse(
        data={"suggestion": suggestion, "language": req.language},
        search_depth=search_depth,
    )


@router.post("/generate", response_model=NoriResponse)
async def code_generate(req: GenerateRequest, llm: LlmDep, emb: EmbeddingDep):
    """코드 생성 — 설명으로부터 완전한 코드 생성"""
    rag_context = ""
    search_depth = "none"

    if emb.is_available:
        collections = None
        if req.project_type == "egov":
            collections = ["egov", "javadoc", "community"]
        results = await emb.search(
            query=req.description,
            top_k=5,
            collections=collections,
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:800] for r in results)
            search_depth = "chroma"

    code = await llm.generate_code(
        description=req.description,
        language=req.language,
        project_context=req.project_context,
        rag_context=rag_context,
    )
    return NoriResponse(
        data={"code": code, "language": req.language},
        search_depth=search_depth,
    )


@router.post("/refactor", response_model=NoriResponse)
async def code_refactor(req: RefactorRequest, llm: LlmDep, emb: EmbeddingDep):
    """리팩토링 제안 — 코드 개선 방안 + 변환 코드"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(query=f"refactor {req.language} best practice", top_k=3)
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    result = await llm.refactor_code(
        code=req.code,
        instruction=req.instruction,
        language=req.language,
        rag_context=rag_context,
    )
    return NoriResponse(data={"refactored_code": result})


@router.post("/test/generate", response_model=NoriResponse)
async def test_generate(req: TestGenerateRequest, llm: LlmDep):
    """테스트 코드 생성 — JUnit 테스트 자동 생성"""
    result = await llm.generate_test(
        code=req.code,
        language=req.language,
    )
    return NoriResponse(data={"test_code": result, "framework": req.test_framework})
