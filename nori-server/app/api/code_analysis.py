"""
코드 분석/이해 API — 코드 설명, 클래스 분석, 코드 리뷰, JavaDoc 생성
"""
import logging

from fastapi import APIRouter, Depends

from app.api.deps import LlmDep, EmbeddingDep, verify_api_key
from app.api.models import (
    ExplainRequest, ClassExplainRequest, ReviewRequest,
    DocGenerateRequest, NoriResponse,
)

logger = logging.getLogger("nori-server")
router = APIRouter(
    prefix="/api/v1",
    tags=["code-analysis"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("/explain", response_model=NoriResponse)
async def explain_code(req: ExplainRequest, llm: LlmDep, emb: EmbeddingDep):
    """코드 설명 — 선택한 코드 블록을 한국어로 설명"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(query=req.code[:300], top_k=3)
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    explanation = await llm.explain_code(
        code=req.code,
        language=req.language,
        rag_context=rag_context,
    )
    return NoriResponse(data={"explanation": explanation})


@router.post("/explain/class", response_model=NoriResponse)
async def explain_class(req: ClassExplainRequest, llm: LlmDep, emb: EmbeddingDep):
    """클래스 구조 분석 — 상속관계, 의존성, 역할 설명"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(query=req.code[:300], top_k=3)
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    analysis = await llm.explain_class(
        code=req.code,
        class_info=req.class_info,
        rag_context=rag_context,
    )
    return NoriResponse(data={"analysis": analysis})


@router.post("/review", response_model=NoriResponse)
async def review_code(req: ReviewRequest, llm: LlmDep, emb: EmbeddingDep):
    """코드 리뷰 — 버그, 성능, 보안 취약점 분석"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query=f"code review {req.language} common issues",
            top_k=3,
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    review = await llm.review_code(
        code=req.code,
        language=req.language,
        rag_context=rag_context,
    )
    return NoriResponse(data={"review": review})


@router.post("/doc/generate", response_model=NoriResponse)
async def generate_doc(req: DocGenerateRequest, llm: LlmDep):
    """JavaDoc 생성 — 클래스/메서드에 주석 자동 생성"""
    javadoc = await llm.generate_javadoc(code=req.code)
    return NoriResponse(data={"documented_code": javadoc})
