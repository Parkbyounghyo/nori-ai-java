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


# ── 선택적 히스토리 조회 ──

def get_selected_messages(user_id: str, session_id: str, indices: list[int]) -> list[dict]:
    """세션에서 체크박스로 선택한 대화 쌍만 LLM history 형태로 반환

    indices: Q&A 쌍 인덱스 (0-based). index=0 → 첫 번째 user+assistant 쌍.
    반환: [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}, ...]
    """
    session = _load_session(user_id, session_id)
    if not session:
        return []
    pairs = _extract_qa_pairs(session.get("messages", []))
    selected = []
    for idx in sorted(set(indices)):
        if 0 <= idx < len(pairs):
            pair = pairs[idx]
            selected.append({"role": "user", "content": pair["user"]})
            if pair.get("assistant"):
                selected.append({"role": "assistant", "content": pair["assistant"]})
    return selected


def get_default_history(user_id: str, session_id: str, count: int = 5) -> list[dict]:
    """선택이 없을 때 기본 히스토리 — 최근 count개 Q&A 쌍 반환

    가이드 기준: 사용자가 별도 선택하지 않으면 최근 5개 디폴트 전송.
    """
    session = _load_session(user_id, session_id)
    if not session:
        return []
    pairs = _extract_qa_pairs(session.get("messages", []))
    recent = pairs[-count:] if len(pairs) > count else pairs
    history = []
    for pair in recent:
        history.append({"role": "user", "content": pair["user"]})
        if pair.get("assistant"):
            history.append({"role": "assistant", "content": pair["assistant"]})
    return history


def list_qa_pairs(user_id: str, session_id: str) -> list[dict]:
    """세션의 Q&A 쌍 목록 반환 — 프론트에서 체크박스 목록 렌더링용

    반환: [{"index": 0, "question": "...(60자 요약)", "has_answer": True}, ...]
    """
    session = _load_session(user_id, session_id)
    if not session:
        return []
    pairs = _extract_qa_pairs(session.get("messages", []))
    result = []
    for i, pair in enumerate(pairs):
        q = pair["user"].split("\n")[0].strip()
        if len(q) > 60:
            q = q[:57] + "..."
        result.append({
            "index": i,
            "question": q or "(빈 질문)",
            "has_answer": bool(pair.get("assistant")),
        })
    return result


def build_history_from_request(
    user_id: str,
    session_id: str,
    client_history: list[dict],
    selected_indices: list[int] | None = None,
    default_count: int = 5,
) -> list[dict]:
    """히스토리 결정 통합 함수 — 클라이언트 전송 or 선택 인덱스 or 디폴트

    우선순위:
    1. selected_indices 제공 → 해당 Q&A 쌍만 추출
    2. client_history 제공 → 그대로 사용 (클라이언트가 이미 선별함)
    3. 둘 다 없음 → 세션에서 최근 default_count개 Q&A 쌍
    """
    if selected_indices is not None and session_id:
        return get_selected_messages(user_id, session_id, selected_indices)
    if client_history:
        return client_history
    if session_id:
        return get_default_history(user_id, session_id, default_count)
    return []


# ── 내부 함수 ──

def _extract_qa_pairs(messages: list) -> list[dict]:
    """메시지 배열에서 Q&A 쌍 추출 — user 메시지 기준으로 쌍을 구성"""
    pairs = []
    current_pair = None
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            if current_pair:
                pairs.append(current_pair)
            current_pair = {"user": content, "assistant": ""}
        elif role == "assistant" and current_pair:
            current_pair["assistant"] = content
    if current_pair:
        pairs.append(current_pair)
    return pairs

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
