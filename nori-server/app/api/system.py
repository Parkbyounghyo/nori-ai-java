"""
시스템 API — 헬스체크, 모델 목록, 설정 변경
"""
import time

from fastapi import APIRouter, Depends

from app.api.deps import (
    EmbeddingDep, SettingsDep,
    get_start_time, verify_api_key,
)
from app.api.models import HealthResponse, ConfigUpdateRequest, NoriResponse

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health_check(settings: SettingsDep, embedding: EmbeddingDep):
    """서버 상태 확인 (DB 연결, LLM 설정 포함)"""
    vector_stats = await embedding.get_stats()
    current_model = (
        settings.ollama_model if settings.ai_provider == "ollama"
        else settings.ai_model
    )
    return HealthResponse(
        status="healthy",
        ai_provider=settings.ai_provider,
        ai_model=current_model,
        vector_db=vector_stats,
        uptime_seconds=round(time.time() - get_start_time(), 1),
    )


@router.get("/models")
async def list_models(settings: SettingsDep):
    """사용 가능한 AI 모델 목록"""
    models = []
    if settings.ai_provider == "openai":
        models = [
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "type": "cloud"},
            {"id": "gpt-4o", "name": "GPT-4o", "type": "cloud"},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "type": "cloud"},
        ]
    elif settings.ai_provider == "ollama":
        # Ollama에서 실제 설치된 모델 목록 조회
        import httpx
        try:
            async with httpx.AsyncClient(
                base_url=settings.ollama_base_url, timeout=5.0
            ) as client:
                resp = await client.get("/api/tags")
                resp.raise_for_status()
                for m in resp.json().get("models", []):
                    models.append({
                        "id": m["name"],
                        "name": m["name"],
                        "size_gb": round(m.get("size", 0) / 1e9, 1),
                        "type": "local",
                    })
        except Exception:
            models = [{"id": settings.ollama_model, "name": settings.ollama_model, "type": "local"}]

    current = settings.ollama_model if settings.ai_provider == "ollama" else settings.ai_model
    return NoriResponse(data={
        "current_model": current,
        "current_provider": settings.ai_provider,
        "available_models": models,
    })


@router.post("/config", dependencies=[Depends(verify_api_key)])
async def update_config(req: ConfigUpdateRequest, settings: SettingsDep):
    """런타임 설정 변경 (모델, temperature 등)"""
    changes = {}
    if req.ai_model is not None:
        settings.ai_model = req.ai_model
        changes["ai_model"] = req.ai_model
    if req.ai_temperature is not None:
        settings.ai_temperature = req.ai_temperature
        changes["ai_temperature"] = req.ai_temperature
    if req.ai_max_tokens is not None:
        settings.ai_max_tokens = req.ai_max_tokens
        changes["ai_max_tokens"] = req.ai_max_tokens

    return NoriResponse(data={"updated": changes})
