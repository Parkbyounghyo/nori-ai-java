"""
프로젝트 프로필 API — 사용자별 프로필 저장·갱신·임베딩

볼트 권장: 프로필만 서버에 저장, 백그라운드 임베딩 후 채팅에서 활용.
"""
import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.deps import EmbeddingDep, verify_api_key
from app.api.models import NoriResponse
from app.config.settings import get_settings
from app.service import profile_store

logger = logging.getLogger("nori-server")

router = APIRouter(
    prefix="/api/v1/profile",
    tags=["profile"],
    dependencies=[Depends(verify_api_key)],
)


def _user_id(x_user_id: str | None = None) -> str:
    return x_user_id or "default"


# ── 요청 모델 ──
from pydantic import BaseModel, Field


class SourceFileItem(BaseModel):
    path: str = Field(..., description="파일 경로")
    content: str = Field("", description="파일 내용 (구조 추출용)")


class ProfileUploadRequest(BaseModel):
    content: str = Field(..., description="프로필 내용 (.nori-profile.md 형식)")
    user_id: str = Field("default", description="사용자 ID")
    project_id: str = Field("", description="프로젝트 ID (비면 content 기반 자동생성)")
    name: str = Field("", description="프로젝트 표시명")
    source_files: list[SourceFileItem] = Field(default_factory=list, description="소스 파일 목록 (클래스·메서드·주석 추출)")


@router.post("/upload", response_model=NoriResponse)
async def upload_profile(
    req: ProfileUploadRequest,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
):
    """프로필 업로드 — 저장 후 백그라운드 임베딩 (소스 구조 포함)"""
    settings = get_settings()
    uid = _user_id(x_user_id or req.user_id)
    meta = profile_store.save_profile(
        settings,
        content=req.content,
        user_id=uid,
        project_id=req.project_id or None,
        name=req.name,
    )
    # 소스 구조 저장 (source_files 제공 시)
    if req.source_files:
        files = [{"path": f.path, "content": f.content} for f in req.source_files]
        cnt = profile_store.save_source_structure(settings, uid, meta["project_id"], files)
        logger.info("[소스구조] %d개 파일 → %d개 항목", len(req.source_files), cnt)
    # 백그라운드 임베딩
    asyncio.create_task(_embed_profile_background(uid, meta["project_id"]))
    return NoriResponse(data={
        "project_id": meta["project_id"],
        "name": meta["name"],
        "embedding_status": "pending",
        "message": "프로필 저장됨. 임베딩은 백그라운드에서 진행됩니다.",
    })


async def _embed_profile_background(user_id: str, project_id: str):
    """백그라운드: 프로필 청킹 → 임베딩"""
    from app.api.deps import get_embedding_service
    settings = get_settings()
    profile_store.set_embedding_status(settings, user_id, project_id, "processing")
    try:
        content = profile_store.load_profile(settings, user_id, project_id)
        if not content:
            return
        emb = get_embedding_service()
        if not emb.is_available:
            profile_store.set_embedding_status(settings, user_id, project_id, "pending")
            return
        prefix = f"u_{user_id[:8]}_p_{project_id[:12]}"
        documents = []
        metadatas = []
        ids = []
        idx = 0
        for i, (chunk, m) in enumerate(profile_store.chunk_profile_for_embedding(content)):
            documents.append(chunk)
            metadatas.append({"user_id": user_id, "project_id": project_id, **m})
            ids.append(f"{prefix}_p_{i}")
            idx = i + 1
        # 소스 구조 청크 추가
        source_items = profile_store.load_source_structure(settings, user_id, project_id)
        for i, (chunk, m) in enumerate(profile_store.chunk_source_structure_for_embedding(source_items)):
            documents.append(chunk)
            metadatas.append({"user_id": user_id, "project_id": project_id, **m})
            ids.append(f"{prefix}_s_{i}")
        if documents:
            col_name = profile_store.COLLECTION_PROFILES
            try:
                await emb.delete_by_metadata(col_name, {"$and": [{"user_id": user_id}, {"project_id": project_id}]})
            except Exception:
                pass
            await emb.add_documents(col_name, documents, metadatas, ids)
        profile_store.set_embedding_status(settings, user_id, project_id, "ready")
        logger.info("[프로필임베딩] user=%s project=%s %d청크 완료", user_id, project_id, len(documents))
    except Exception as e:
        logger.error("[프로필임베딩] 실패: %s", e, exc_info=True)
        profile_store.set_embedding_status(settings, user_id, project_id, "failed")


@router.get("/list", response_model=NoriResponse)
async def list_projects(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
):
    """사용자별 프로젝트 목록"""
    settings = get_settings()
    uid = _user_id(x_user_id)
    projects = profile_store.list_projects(settings, uid)
    return NoriResponse(data={"projects": projects})


@router.get("/{project_id}", response_model=NoriResponse)
async def get_profile(
    project_id: str,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
):
    """프로필 내용 조회"""
    settings = get_settings()
    uid = _user_id(x_user_id)
    content = profile_store.load_profile(settings, uid, project_id)
    if content is None:
        return NoriResponse(success=False, error="프로필을 찾을 수 없습니다")
    meta = profile_store.load_meta(settings, uid, project_id)
    return NoriResponse(data={
        "content": content,
        "meta": meta or {},
    })


@router.get("/{project_id}/status", response_model=NoriResponse)
async def get_profile_status(
    project_id: str,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
):
    """프로필 임베딩 상태"""
    settings = get_settings()
    uid = _user_id(x_user_id)
    meta = profile_store.load_meta(settings, uid, project_id)
    if not meta:
        return NoriResponse(success=False, error="프로필을 찾을 수 없습니다")
    return NoriResponse(data={
        "project_id": project_id,
        "embedding_status": meta.get("embedding_status", "unknown"),
        "updated_at": meta.get("updated_at"),
    })


class ProfileUpdateRequest(BaseModel):
    content: str = Field("", description="프로필 내용 (비면 기존 유지)")
    source_files: list[SourceFileItem] = Field(default_factory=list, description="소스 파일 (클래스·메서드·주석 추출)")


@router.put("/{project_id}/update", response_model=NoriResponse)
async def update_profile(
    project_id: str,
    req: ProfileUpdateRequest,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
):
    """프로필·소스 구조 업데이트 — 우측 메뉴 '프로젝트 갱신'에서 호출"""
    settings = get_settings()
    uid = _user_id(x_user_id)
    if not profile_store.load_profile(settings, uid, project_id) and not req.content:
        return NoriResponse(success=False, error="프로젝트를 찾을 수 없습니다")
    if req.content:
        profile_store.save_profile(settings, req.content, uid, project_id)
    if req.source_files:
        files = [{"path": f.path, "content": f.content} for f in req.source_files]
        cnt = profile_store.save_source_structure(settings, uid, project_id, files)
        logger.info("[업데이트] project=%s 소스구조 %d개 파일 → %d항목", project_id, len(req.source_files), cnt)
    asyncio.create_task(_embed_profile_background(uid, project_id))
    return NoriResponse(data={
        "message": "업데이트를 반영했습니다. 임베딩은 백그라운드에서 진행됩니다.",
        "embedding_status": "processing",
    })


@router.put("/{project_id}/reembed", response_model=NoriResponse)
async def reembed_profile(
    project_id: str,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
):
    """프로필 전체 재임베딩"""
    settings = get_settings()
    uid = _user_id(x_user_id)
    if not profile_store.load_profile(settings, uid, project_id):
        return NoriResponse(success=False, error="프로필을 찾을 수 없습니다")
    asyncio.create_task(_embed_profile_background(uid, project_id))
    return NoriResponse(data={
        "message": "재임베딩을 백그라운드에서 시작했습니다.",
        "embedding_status": "processing",
    })


@router.delete("/{project_id}", response_model=NoriResponse)
async def delete_profile(
    project_id: str,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
):
    """프로필 삭제"""
    settings = get_settings()
    uid = _user_id(x_user_id)
    if profile_store.delete_profile(settings, uid, project_id):
        return NoriResponse(data={"message": "삭제되었습니다"})
    return NoriResponse(success=False, error="프로필을 찾을 수 없습니다")
