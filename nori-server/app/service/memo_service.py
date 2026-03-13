"""
프로젝트 메모 서비스 — DB 스키마, API 매핑, 에러 로그, 코딩 컨벤션 관리
파일 기반 JSON 저장소로, LLM 컨텍스트에 주입하여 AI 응답 품질 향상
"""
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nori-server")

# 메모 타입 상수
MEMO_SCHEMA = "db-schema"
MEMO_API_MAP = "api-mapping"
MEMO_ERROR_LOG = "error-log"
MEMO_CONVENTION = "convention"

VALID_TYPES = {MEMO_SCHEMA, MEMO_API_MAP, MEMO_ERROR_LOG, MEMO_CONVENTION}


class MemoService:
    """프로젝트 메모 저장/조회/LLM 컨텍스트 생성 서비스"""

    def __init__(self, data_dir: str = "data/memos"):
        self._dir = Path(data_dir).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"메모 저장 경로: {self._dir}")

    # ── 기본 CRUD ──

    def _path(self, memo_type: str) -> Path:
        return self._dir / f"{memo_type}.json"

    def get(self, memo_type: str) -> dict | list | None:
        p = self._path(memo_type)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, memo_type: str, data: dict | list) -> None:
        p = self._path(memo_type)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"메모 저장: {memo_type}")

    # ── DB 스키마 ──

    def save_schema(self, tables: list[dict]) -> None:
        """DB 스키마 저장: [{table, columns: [{name, type, pk, fk, comment}]}]"""
        data = {
            "updated_at": datetime.now().isoformat(),
            "tables": tables,
        }
        self.save(MEMO_SCHEMA, data)

    def get_schema(self) -> dict | None:
        return self.get(MEMO_SCHEMA)

    # ── API 매핑 ──

    def save_api_mapping(self, endpoints: list[dict]) -> None:
        """API 매핑 저장: [{method, uri, handler, description}]"""
        data = {
            "updated_at": datetime.now().isoformat(),
            "endpoints": endpoints,
        }
        self.save(MEMO_API_MAP, data)

    def get_api_mapping(self) -> dict | None:
        return self.get(MEMO_API_MAP)

    # ── 에러 로그 ──

    def add_error_log(self, symptom: str, solution: str, category: str = "") -> None:
        """에러 로그 한 건 추가"""
        logs = self.get(MEMO_ERROR_LOG) or []
        logs.append({
            "timestamp": datetime.now().isoformat(),
            "symptom": symptom,
            "solution": solution,
            "category": category,
        })
        self.save(MEMO_ERROR_LOG, logs)

    def get_error_logs(self) -> list:
        return self.get(MEMO_ERROR_LOG) or []

    # ── 코딩 컨벤션 ──

    def save_convention(self, rules: list[str]) -> None:
        """코딩 컨벤션 규칙 리스트 저장"""
        data = {
            "updated_at": datetime.now().isoformat(),
            "rules": rules,
        }
        self.save(MEMO_CONVENTION, data)

    def get_convention(self) -> dict | None:
        return self.get(MEMO_CONVENTION)

    # ── LLM 컨텍스트 생성 ──

    def get_context(self) -> str:
        """모든 메모를 LLM 시스템 프롬프트에 주입할 문자열로 조합"""
        parts = []

        # 코딩 컨벤션
        conv = self.get_convention()
        if conv and conv.get("rules"):
            rules_text = "\n".join(f"- {r}" for r in conv["rules"])
            parts.append(f"[오빠의 코딩 컨벤션]\n{rules_text}")

        # DB 스키마
        schema = self.get_schema()
        if schema and schema.get("tables"):
            lines = []
            for t in schema["tables"]:
                cols = ", ".join(
                    f"{c.get('name','?')}({c.get('type','?')}"
                    + (",PK" if c.get("pk") else "")
                    + (f",FK→{c['fk']}" if c.get("fk") else "")
                    + ")"
                    for c in t.get("columns", [])
                )
                lines.append(f"  {t.get('table','?')}: {cols}")
            parts.append("[프로젝트 DB 스키마]\n" + "\n".join(lines))

        # API 매핑
        api = self.get_api_mapping()
        if api and api.get("endpoints"):
            lines = []
            for ep in api["endpoints"]:
                line = f"  {ep.get('method','?')} {ep.get('uri','?')}"
                if ep.get("handler"):
                    line += f" → {ep['handler']}"
                if ep.get("description"):
                    line += f" // {ep['description']}"
                lines.append(line)
            parts.append("[프로젝트 API 엔드포인트]\n" + "\n".join(lines))

        # 에러 로그 (최근 20개)
        errors = self.get_error_logs()
        if errors:
            recent = errors[-20:]
            lines = [f"  [{e.get('category','')}] {e['symptom']} → {e['solution']}" for e in recent]
            parts.append("[에러 해결 기록]\n" + "\n".join(lines))

        return "\n\n".join(parts)
