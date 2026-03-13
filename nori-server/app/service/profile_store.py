"""프로젝트 프로필 저장소 — 사용자·프로젝트별 분리 저장

볼트 권장: 프로필만 서버에 저장, 임베딩 후 채팅/검색에 활용.
"""
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


def _safe_id(s: str) -> str:
    """경로에 안전한 ID 생성"""
    if not s or not s.strip():
        return "default"
    h = hashlib.sha256(s.strip().encode()).hexdigest()[:12]
    safe = re.sub(r"[^\w\-]", "_", s.strip())[:24]
    return f"{safe}_{h}" if safe else h


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
    """프로필 저장 및 메타정보 반환"""
    pid = project_id or _safe_id(name or content[:80])
    root = _profiles_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    d = _project_dir(settings, user_id, pid)
    d.mkdir(parents=True, exist_ok=True)

    (d / "profile.md").write_text(content, encoding="utf-8")
    meta = {
        "user_id": user_id or "default",
        "project_id": pid,
        "name": name or pid,
        "updated_at": datetime.now().isoformat(),
        "embedding_status": "pending",
        "content_length": len(content),
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[프로필저장] user=%s project=%s %d자", user_id, pid, len(content))
    return meta


def load_profile(settings, user_id: str, project_id: str) -> str | None:
    """프로필 내용 로드"""
    d = _project_dir(settings, user_id, project_id)
    p = d / "profile.md"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def load_meta(settings, user_id: str, project_id: str) -> dict | None:
    """메타정보 로드"""
    d = _project_dir(settings, user_id, project_id)
    p = d / "meta.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def set_embedding_status(settings, user_id: str, project_id: str, status: str):
    """임베딩 상태 갱신"""
    meta = load_meta(settings, user_id, project_id)
    if not meta:
        return
    meta["embedding_status"] = status
    d = _project_dir(settings, user_id, project_id)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def list_projects(settings, user_id: str = "default") -> list[dict]:
    """사용자별 프로젝트 목록"""
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
            pass
    result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return result


def delete_profile(settings, user_id: str, project_id: str) -> bool:
    """프로필 삭제"""
    import shutil
    d = _project_dir(settings, user_id, project_id)
    if not d.exists():
        return False
    shutil.rmtree(d)
    logger.info("[프로필삭제] user=%s project=%s", user_id, project_id)
    return True


def save_source_structure(settings, user_id: str, project_id: str, files: list[dict]) -> int:
    """소스 파일에서 구조 추출 후 저장. 반환: 추출된 아이템 수."""
    from app.service.source_extractor import extract_file_structure
    items_all = []
    for f in files:
        path = f.get("path", "") or f.get("file_path", "")
        content = f.get("content", "")
        if not path or not content:
            continue
        items = extract_file_structure(path, content)
        for it in items:
            it["file_path"] = path
            items_all.append(it)
    d = _project_dir(settings, user_id, project_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "source_structure.json").write_text(
        json.dumps({"items": items_all}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(items_all)


def load_source_structure(settings, user_id: str, project_id: str) -> list[dict]:
    """저장된 소스 구조 로드"""
    d = _project_dir(settings, user_id, project_id)
    p = d / "source_structure.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("items", [])


def chunk_profile_for_embedding(content: str, max_chars: int = 1200) -> Iterator[tuple[str, dict]]:
    """프로필을 임베딩용 청크로 분할. (텍스트, 메타) 쌍 생성."""
    # 1. 경로: 블록 단위 (파일 1개 = 1청크)
    path_blocks = re.findall(
        r'(경로:\s*[^\n]+(?:\s*\|\s*설명:[^\n]+)?)',
        content
    )
    for i, block in enumerate(path_blocks):
        if len(block.strip()) > 50:
            yield block.strip(), {"type": "path", "index": i}

    # 2. 기능 요약 섹션
    summary_match = re.search(r'(## 🔍 프로젝트 기능 요약.*?)(?=\n## [^#]|\Z)', content, re.DOTALL)
    if summary_match:
        summary = summary_match.group(1).strip()
        # 하위 #### 블록별 분할
        parts = re.split(r'(?=####\s+\d+\.)', summary)
        for i, part in enumerate(parts):
            part = part.strip()
            if len(part) > 80:
                yield part[:max_chars], {"type": "summary", "index": i}

    # 3. 나머지: 큰 덩어리로
    rest = content
    for m in path_blocks:
        rest = rest.replace(m, "", 1)
    if summary_match:
        rest = rest.replace(summary_match.group(1), "", 1)
    rest = re.sub(r'\n{3,}', '\n\n', rest).strip()
    if len(rest) > 100:
        for i in range(0, len(rest), max_chars):
            chunk = rest[i : i + max_chars]
            if len(chunk.strip()) > 50:
                yield chunk, {"type": "rest", "offset": i}


def chunk_source_structure_for_embedding(items: list[dict], max_per_chunk: int = 10) -> Iterator[tuple[str, dict]]:
    """소스 구조를 임베딩용 청크로. 파일별 또는 N개씩 묶음."""
    from collections import defaultdict
    by_file = defaultdict(list)
    for it in items:
        path = it.get("file_path", "")
        name = it.get("name", "")
        comment = it.get("comment", "").strip()
        line = it.get("line", 0)
        text = f"{name} — {comment}" if comment else name
        if text:
            by_file[path].append(text)
    for path, texts in by_file.items():
        chunk = "\n".join(texts[:max_per_chunk * 2])
        if len(chunk.strip()) > 20:
            yield chunk, {"type": "source_structure", "file_path": path}
