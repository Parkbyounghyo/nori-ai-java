"""Profile storage and embedding chunk helpers."""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("nori-server")

PROFILES_DIR = "profiles"
COLLECTION_PROFILES = "profiles"


def _safe_id(value: str) -> str:
    """Build a filesystem-safe stable identifier."""
    if not value or not value.strip():
        return "default"
    digest = hashlib.sha256(value.strip().encode()).hexdigest()[:12]
    safe = re.sub(r"[^\w\-]", "_", value.strip())[:24]
    return f"{safe}_{digest}" if safe else digest


def _profiles_root(settings) -> Path:
    return settings.data_path / PROFILES_DIR


def _project_dir(settings, user_id: str, project_id: str) -> Path:
    root = _profiles_root(settings)
    uid = _safe_id(user_id or "default")
    pid = _safe_id(project_id)
    return root / uid / pid


def save_profile(
    settings,
    content: str,
    user_id: str = "default",
    project_id: str | None = None,
    name: str = "",
) -> dict:
    """Save a profile document and return its metadata."""
    pid = project_id or _safe_id(name or content[:80])
    root = _profiles_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    directory = _project_dir(settings, user_id, pid)
    directory.mkdir(parents=True, exist_ok=True)

    (directory / "profile.md").write_text(content, encoding="utf-8")
    meta = {
        "user_id": user_id or "default",
        "project_id": pid,
        "name": name or pid,
        "updated_at": datetime.now().isoformat(),
        "embedding_status": "pending",
        "content_length": len(content),
    }
    (directory / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[profile-save] user=%s project=%s %d chars", user_id, pid, len(content))
    return meta


def load_profile(settings, user_id: str, project_id: str) -> str | None:
    """Load a profile document."""
    directory = _project_dir(settings, user_id, project_id)
    profile_path = directory / "profile.md"
    if not profile_path.exists():
        return None
    return profile_path.read_text(encoding="utf-8")


def load_meta(settings, user_id: str, project_id: str) -> dict | None:
    """Load profile metadata."""
    directory = _project_dir(settings, user_id, project_id)
    meta_path = directory / "meta.json"
    if not meta_path.exists():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def set_embedding_status(settings, user_id: str, project_id: str, status: str):
    """Update embedding status in metadata."""
    meta = load_meta(settings, user_id, project_id)
    if not meta:
        return
    meta["embedding_status"] = status
    directory = _project_dir(settings, user_id, project_id)
    (directory / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_projects(settings, user_id: str = "default") -> list[dict]:
    """List projects saved for a user."""
    root = _profiles_root(settings)
    uid = _safe_id(user_id or "default")
    user_dir = root / uid
    if not user_dir.exists():
        return []

    result = []
    for project_dir in user_dir.iterdir():
        if not project_dir.is_dir():
            continue
        meta_path = project_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["project_id"] = project_dir.name
            result.append(meta)
        except Exception:
            continue
    result.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return result


def delete_profile(settings, user_id: str, project_id: str) -> bool:
    """Delete a saved profile."""
    import shutil

    directory = _project_dir(settings, user_id, project_id)
    if not directory.exists():
        return False
    shutil.rmtree(directory)
    logger.info("[profile-delete] user=%s project=%s", user_id, project_id)
    return True


def save_server_context(settings, user_id: str, project_id: str, ctx: dict):
    """Save server.xml/context.xml/workspace tree data."""
    directory = _project_dir(settings, user_id, project_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "server_context.json").write_text(
        json.dumps(ctx, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[server-context-save] user=%s project=%s keys=%s", user_id, project_id, list(ctx.keys()))


def load_server_context(settings, user_id: str, project_id: str) -> dict:
    """Load saved server context."""
    directory = _project_dir(settings, user_id, project_id)
    server_context_path = directory / "server_context.json"
    if not server_context_path.exists():
        return {}
    return json.loads(server_context_path.read_text(encoding="utf-8"))


def save_source_structure(settings, user_id: str, project_id: str, files: list[dict]) -> int:
    """Extract and persist source structure records."""
    from app.service.source_extractor import (
        extract_file_structure, classify_asset, build_reference_item,
    )

    items_all = []
    for item in files:
        path = item.get("path", "") or item.get("file_path", "")
        content = item.get("content", "")
        if not path or not content:
            continue

        classification = classify_asset(path)

        if classification["index_mode"] == "reference_only":
            # 템플릿/에디터 파일 → 위치 참조만 저장 (청킹 안 함)
            items_all.append(build_reference_item(path, classification, project_id))
            continue

        # 업무 코드 → 전체 구조 추출
        for extracted in extract_file_structure(path, content):
            extracted["file_path"] = path
            extracted["asset_type"] = "business_code"
            extracted["index_mode"] = "full"
            if not extracted.get("project"):
                normalized = path.replace("\\", "/").strip("/")
                extracted["project"] = normalized.split("/", 1)[0] if normalized else project_id
            items_all.append(extracted)

    directory = _project_dir(settings, user_id, project_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "source_structure.json").write_text(
        json.dumps({"items": items_all}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(items_all)


def load_source_structure(settings, user_id: str, project_id: str) -> list[dict]:
    """Load saved source structure items."""
    directory = _project_dir(settings, user_id, project_id)
    source_structure_path = directory / "source_structure.json"
    if not source_structure_path.exists():
        return []
    data = json.loads(source_structure_path.read_text(encoding="utf-8"))
    return data.get("items", [])


def chunk_profile_for_embedding(content: str, max_chars: int = 1200) -> Iterator[tuple[str, dict]]:
    """Split a profile document into embedding chunks."""
    path_blocks = re.findall(r"(경로:\s*[^\n]+(?:\s*\|\s*설명:[^\n]+)?)", content)
    for index, block in enumerate(path_blocks):
        if len(block.strip()) > 50:
            yield block.strip(), {"type": "path", "index": index}

    summary_match = re.search(r"(## [^\n]*프로젝트 기능 요약.*?)(?=\n## [^#]|\Z)", content, re.DOTALL)
    if summary_match:
        summary = summary_match.group(1).strip()
        for index, part in enumerate(re.split(r"(?=####\s+\d+\.)", summary)):
            part = part.strip()
            if len(part) > 80:
                yield part[:max_chars], {"type": "summary", "index": index}

    rest = content
    for block in path_blocks:
        rest = rest.replace(block, "", 1)
    if summary_match:
        rest = rest.replace(summary_match.group(1), "", 1)
    rest = re.sub(r"\n{3,}", "\n\n", rest).strip()
    if len(rest) > 100:
        for offset in range(0, len(rest), max_chars):
            chunk = rest[offset:offset + max_chars]
            if len(chunk.strip()) > 50:
                yield chunk, {"type": "rest", "offset": offset}


def chunk_source_structure_for_embedding(items: list[dict], max_per_chunk: int = 10) -> Iterator[tuple[str, dict]]:
    """Convert source structure items into fine-grained embedding chunks.

    index_mode 기반으로 대상 콜렉션 분리:
    - full → 'custom' (business code)
    - reference_only → 'project-templates'
    """
    from app.service.source_extractor import structure_to_chunks

    by_file: dict[str, list[dict]] = {}
    for item in items:
        path = item.get("file_path", "")
        if not path:
            continue
        by_file.setdefault(path, []).append(item)

    for path, file_items in by_file.items():
        # 파일 내 첫 아이템의 index_mode로 콜렉션 결정
        first_mode = file_items[0].get("index_mode", "full")
        target_collection = "project-templates" if first_mode == "reference_only" else "custom"

        emitted = 0
        for chunk, meta in structure_to_chunks(file_items, path, max_per_chunk=max_per_chunk):
            if len(chunk.strip()) <= 20:
                continue
            emitted += 1
            yield chunk, {**meta, "target_collection": target_collection}
        if emitted == 0:
            names = [item.get("name", "") for item in file_items if item.get("name")]
            if names:
                yield "\n".join(names[:max_per_chunk]), {
                    "type": "source_structure",
                    "file_path": path,
                    "target_collection": target_collection,
                }
