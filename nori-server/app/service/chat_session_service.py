"""
채팅 세션 관리 서비스 — 대화 단위 관리 (새 채팅, 대화 목록, 히스토리)

세션 = 하나의 대화 스레드. 사용자가 "새 채팅"을 누르면 새 세션 시작.
이전 세션의 대화 내용은 파일로 저장되어 목록에서 다시 불러올 수 있음.
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nori-server")

# 세션 저장 디렉토리
_SESSIONS_DIR = Path("data/chat_sessions")


def _ensure_dir():
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(user_id: str, session_id: str) -> Path:
    return _SESSIONS_DIR / user_id / f"{session_id}.json"


def _user_dir(user_id: str) -> Path:
    return _SESSIONS_DIR / user_id


def _generate_title(messages: list) -> str:
    """대화 내용에서 세션 제목 자동 생성 — 첫 번째 user 메시지 기반"""
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content", "")
            # 너무 긴 메시지는 자르기 (에러 스택트레이스 등)
            first_line = content.split("\n")[0].strip()
            if len(first_line) > 60:
                return first_line[:57] + "..."
            return first_line or "새 대화"
    return "새 대화"


def create_session(user_id: str = "default", title: str = "") -> dict:
    """새 채팅 세션 생성"""
    _ensure_dir()
    session_id = str(uuid.uuid4())[:8] + "-" + datetime.now().strftime("%m%d%H%M")
    session = {
        "session_id": session_id,
        "user_id": user_id,
        "title": title or "새 대화",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "messages": [],
        "message_count": 0,
    }
    _save_session(user_id, session_id, session)
    logger.info("[채팅세션] 생성: user=%s, session=%s", user_id, session_id)
    return session


def save_messages(user_id: str, session_id: str, messages: list) -> dict:
    """세션에 메시지 저장 (전체 교체)"""
    session = _load_session(user_id, session_id)
    if not session:
        session = create_session(user_id)
        session_id = session["session_id"]

    session["messages"] = messages
    session["message_count"] = len(messages)
    session["updated_at"] = datetime.now().isoformat()

    # 제목이 기본값이면 자동 생성
    if session.get("title") in ("새 대화", ""):
        session["title"] = _generate_title(messages)

    _save_session(user_id, session_id, session)
    return {"session_id": session_id, "message_count": len(messages)}


def append_message(user_id: str, session_id: str, role: str, content: str) -> dict:
    """세션에 메시지 1개 추가"""
    session = _load_session(user_id, session_id)
    if not session:
        session = create_session(user_id)
        session_id = session["session_id"]

    session["messages"].append({"role": role, "content": content})
    session["message_count"] = len(session["messages"])
    session["updated_at"] = datetime.now().isoformat()

    if session.get("title") in ("새 대화", "") and role == "user":
        session["title"] = _generate_title(session["messages"])

    _save_session(user_id, session_id, session)
    return {"session_id": session_id, "message_count": session["message_count"]}


def list_sessions(user_id: str = "default", limit: int = 50) -> list[dict]:
    """사용자의 채팅 세션 목록 반환 (최신 순)"""
    _ensure_dir()
    user_path = _user_dir(user_id)
    if not user_path.exists():
        return []

    sessions = []
    for f in user_path.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "session_id": data.get("session_id", f.stem),
                "title": data.get("title", "대화"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "message_count": data.get("message_count", 0),
            })
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[채팅세션] 파일 읽기 실패: %s — %s", f, e)

    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions[:limit]


def get_session(user_id: str, session_id: str) -> Optional[dict]:
    """세션 전체 데이터 조회 (메시지 포함)"""
    return _load_session(user_id, session_id)


def delete_session(user_id: str, session_id: str) -> bool:
    """세션 삭제"""
    path = _session_path(user_id, session_id)
    if path.exists():
        path.unlink()
        logger.info("[채팅세션] 삭제: user=%s, session=%s", user_id, session_id)
        return True
    return False


def rename_session(user_id: str, session_id: str, title: str) -> Optional[dict]:
    """세션 제목 변경"""
    session = _load_session(user_id, session_id)
    if not session:
        return None
    session["title"] = title
    session["updated_at"] = datetime.now().isoformat()
    _save_session(user_id, session_id, session)
    return {"session_id": session_id, "title": title}


# ── 내부 함수 ──

def _load_session(user_id: str, session_id: str) -> Optional[dict]:
    path = _session_path(user_id, session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[채팅세션] 로드 실패: %s — %s", path, e)
        return None


def _save_session(user_id: str, session_id: str, data: dict):
    path = _session_path(user_id, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
