"""
PL 워크플로우 서비스 — 파일 분석 → TODO 관리 → 순차 소스 제안 → 테스트 검증 → 보고서
노리 AI의 PL 역할 핵심 엔진
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from app.config.settings import Settings
from app.service.llm_service import LlmService

logger = logging.getLogger("nori-server")


class PlWorkflowService:
    """PL 워크플로우 서비스 — 수정 요청을 분할·순차 처리"""

    def __init__(self, data_dir: str = "data/memos"):
        self._dir = Path(data_dir).resolve()
        self._todo_dir = self._dir / "todos"
        self._feedback_dir = self._dir / "feedback"
        self._history_dir = self._dir / "history"
        for d in [self._todo_dir, self._feedback_dir, self._history_dir]:
            d.mkdir(parents=True, exist_ok=True)
        self._llm_service: LlmService | None = None
        logger.info(f"PL 워크플로우 서비스 초기화: {self._dir}")

    def set_llm_service(self, llm_service: LlmService) -> None:
        self._llm_service = llm_service

    # ═══════════════════════════════════════════
    # Step 1. 파일 분석 — 수정 대상 파일 + 순서 결정
    # ═══════════════════════════════════════════

    async def analyze_request(
        self,
        user_request: str,
        project_profile: str = "",
        file_tree: str = "",
        file_contents: dict[str, str] | None = None,
        dependency_map: dict[str, dict] | None = None,
    ) -> dict:
        """
        사용자 요청을 분석하여 수정 대상 파일 목록 + 순서를 반환.
        코드 수정은 하지 않음 — 분석만 수행.
        dependency_map이 있으면 패턴 기반 보완까지 수행.
        """
        if not self._llm_service:
            return {"error": "LLM 서비스가 연결되지 않았습니다"}

        prompt_path = Path("prompts/pl-analyze.txt")
        if prompt_path.exists():
            system_prompt = prompt_path.read_text(encoding="utf-8")
        else:
            system_prompt = self._default_analyze_prompt()

        user_content = f"## 수정 요청\n{user_request}"
        if project_profile:
            user_content += f"\n\n## 프로젝트 프로필\n{project_profile}"
        if file_tree:
            user_content += f"\n\n## 프로젝트 파일 구조\n{file_tree}"
        if dependency_map:
            user_content += "\n\n## 의존관계 맵 (도메인별 관련 파일 그룹)\n```json\n"
            user_content += json.dumps(dependency_map, ensure_ascii=False, indent=2)
            user_content += "\n```"
        if file_contents:
            user_content += "\n\n## 관련 파일 내용"
            for path, content in file_contents.items():
                user_content += f"\n\n### {path}\n```\n{content}\n```"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        result = await self._llm_service._provider.complete(messages)

        # JSON 파싱 시도
        parsed = self._extract_json(result)
        if not parsed:
            return {"raw_response": result, "error": "JSON 파싱 실패"}

        # 의존관계 맵 기반 패턴 보완 — AI가 놓친 파일 자동 추가
        if dependency_map and parsed.get("files"):
            parsed = self._enhance_file_detection(parsed, dependency_map)

        return parsed

    def _enhance_file_detection(
        self, analysis: dict, dependency_map: dict[str, dict]
    ) -> dict:
        """
        AI 분석 결과에서 누락된 관련 파일을 의존관계 맵으로 보완.
        예: BoardMailVO.java 있으면 → BoardMailDAO, BoardMailService 등 자동 포함.
        """
        existing_files = {
            f["file_name"] for f in analysis.get("files", [])
        }
        files = list(analysis["files"])
        added = []

        # AI가 식별한 파일명에서 도메인 키를 추출
        detected_domains = set()
        for f in analysis["files"]:
            fname = f.get("file_name", "")
            for domain_key in dependency_map:
                if domain_key.lower() in fname.lower():
                    detected_domains.add(domain_key)

        # 감지된 도메인의 누락 파일 자동 추가
        layer_order = {"vo": 1, "dao": 2, "mapper_xml": 2, "service": 3,
                       "controller": 4, "jsp": 5, "config": 6}
        layer_labels = {"vo": "VO", "dao": "DAO", "mapper_xml": "Mapper XML",
                        "service": "Service", "controller": "Controller",
                        "jsp": "JSP", "config": "Config"}

        for domain_key in detected_domains:
            group = dependency_map.get(domain_key, {})
            for layer_key, file_entries in group.items():
                if layer_key in ("imports",):
                    continue
                entries = file_entries if isinstance(file_entries, list) else [file_entries]
                for entry in entries:
                    # entry는 {"name": "xxx.java", "path": "src/..."} 또는 문자열
                    if isinstance(entry, dict):
                        fname = entry.get("name", "")
                        fpath = entry.get("path", "")
                    else:
                        fname = str(entry).split("/")[-1] if "/" in str(entry) else str(entry)
                        fpath = str(entry)

                    if fname and fname not in existing_files:
                        base_order = layer_order.get(layer_key, 6)
                        max_order = max(
                            (f["order"] for f in files), default=0
                        )
                        new_order = max_order + 1
                        files.append({
                            "order": new_order,
                            "file_path": fpath,
                            "file_name": fname,
                            "layer": layer_labels.get(layer_key, layer_key.upper()),
                            "action": f"[자동감지] {domain_key} 도메인 관련 파일 — 수정 필요 여부 확인",
                            "dependencies": [],
                            "auto_detected": True,
                        })
                        existing_files.add(fname)
                        added.append(fname)

        if added:
            # 순서 재정렬 (layer 기준)
            files = self._reorder_files(files, layer_order)
            analysis["files"] = files
            original_thinking = analysis.get("thinking", [])
            original_thinking.append(
                f"📎 패턴 매칭으로 {len(added)}개 파일 자동 추가: {', '.join(added)}"
            )
            analysis["thinking"] = original_thinking
            analysis["auto_added"] = added
            logger.info(f"패턴 매칭 자동 추가: {added}")

        return analysis

    @staticmethod
    def _reorder_files(
        files: list[dict], layer_order: dict[str, int]
    ) -> list[dict]:
        """파일 목록을 layer 기준으로 재정렬"""
        label_to_key = {
            "VO": "vo", "DTO": "vo", "DAO": "dao", "Mapper XML": "mapper_xml",
            "Service": "service", "Controller": "controller",
            "JSP": "jsp", "Config": "config",
        }
        def sort_key(f):
            layer = f.get("layer", "")
            key = label_to_key.get(layer, "config")
            return layer_order.get(key, 6)

        sorted_files = sorted(files, key=sort_key)
        for i, f in enumerate(sorted_files):
            f["order"] = i + 1
        return sorted_files

    @staticmethod
    def _default_analyze_prompt() -> str:
        return """당신은 Java/Spring/eGov 프로젝트의 PL(Project Leader)입니다.
사용자의 수정 요청을 분석하여, 프로젝트에서 수정이 필요한 파일 목록과 순서를 정해주세요.

## 응답 형식 (반드시 JSON으로)
```json
{
  "thinking": ["생각 과정 1", "생각 과정 2"],
  "summary": "요청 요약",
  "analysis": "분석 내용",
  "files": [{"order":1,"file_path":"...","file_name":"...","layer":"VO","action":"...","dependencies":[]}],
  "notes": "참고사항"
}
```"""

    # ═══════════════════════════════════════════
    # Step 2. TODO 관리
    # ═══════════════════════════════════════════

    def create_todo(self, user_request: str, analysis: dict) -> dict:
        """분석 결과를 기반으로 TODO 생성 및 파일 저장"""
        todo_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        todo = {
            "id": todo_id,
            "created_at": datetime.now().isoformat(),
            "user_request": user_request,
            "summary": analysis.get("summary", ""),
            "analysis": analysis.get("analysis", ""),
            "notes": analysis.get("notes", ""),
            "items": [],
            "status": "in-progress",
            "completed_summaries": [],
        }

        for file_info in analysis.get("files", []):
            todo["items"].append({
                "order": file_info.get("order", 0),
                "file_path": file_info.get("file_path", ""),
                "file_name": file_info.get("file_name", ""),
                "layer": file_info.get("layer", ""),
                "action": file_info.get("action", ""),
                "dependencies": file_info.get("dependencies", []),
                "status": "pending",
                "test_status": None,
                "test_reason": None,
                "feedback": None,
                "suggested_source": None,
                "start_line": None,
            })

        self._save_todo(todo_id, todo)
        return todo

    def get_todo(self, todo_id: str) -> dict | None:
        """TODO 조회"""
        path = self._todo_dir / f"{todo_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_active_todo(self) -> dict | None:
        """현재 진행 중인 TODO 조회 (가장 최근)"""
        todos = sorted(self._todo_dir.glob("*.json"), reverse=True)
        for p in todos:
            todo = json.loads(p.read_text(encoding="utf-8"))
            if todo.get("status") == "in-progress":
                return todo
        return None

    def list_todos(self) -> list[dict]:
        """모든 TODO 목록 (간략 정보만)"""
        result = []
        for p in sorted(self._todo_dir.glob("*.json"), reverse=True):
            todo = json.loads(p.read_text(encoding="utf-8"))
            result.append({
                "id": todo["id"],
                "created_at": todo["created_at"],
                "summary": todo.get("summary", ""),
                "status": todo.get("status", ""),
                "total": len(todo.get("items", [])),
                "completed": sum(1 for i in todo.get("items", []) if i["status"] == "done"),
            })
        return result

    def update_todo_item(self, todo_id: str, order: int, updates: dict) -> dict | None:
        """TODO 항목 업데이트"""
        todo = self.get_todo(todo_id)
        if not todo:
            return None

        for item in todo["items"]:
            if item["order"] == order:
                item.update(updates)
                break

        # 전체 완료 체크
        all_done = all(
            i["status"] in ("done", "skipped") for i in todo["items"]
        )
        if all_done:
            todo["status"] = "completed"
            todo["completed_at"] = datetime.now().isoformat()

        self._save_todo(todo_id, todo)
        return todo

    def delete_todo(self, todo_id: str) -> bool:
        """TODO 삭제"""
        path = self._todo_dir / f"{todo_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # ═══════════════════════════════════════════
    # Step 3. 순차 소스 제안
    # ═══════════════════════════════════════════

    async def suggest_source(
        self,
        todo_id: str,
        order: int,
        file_content: str,
    ) -> dict:
        """
        TODO 항목에 대한 수정 소스 제안.
        이전 단계 수정 정보를 함께 AI에게 전달하여 일관성 유지.
        """
        if not self._llm_service:
            return {"error": "LLM 서비스가 연결되지 않았습니다"}

        todo = self.get_todo(todo_id)
        if not todo:
            return {"error": "TODO를 찾을 수 없습니다"}

        # 현재 항목 찾기
        current_item = None
        for item in todo["items"]:
            if item["order"] == order:
                current_item = item
                break
        if not current_item:
            return {"error": f"순서 {order}번 항목을 찾을 수 없습니다"}

        # 이전 단계 수정 요약 수집
        prev_summaries = []
        for item in todo["items"]:
            if item["order"] < order and item.get("suggested_source"):
                prev_summaries.append(
                    f"[{item['file_name']}] {item['action']}: "
                    f"L{item.get('start_line', '?')}부터 수정"
                )

        system_prompt = f"""당신은 Java/Spring/eGov 코드 수정 전문가입니다.
사용자의 요청에 따라 해당 파일의 수정 소스를 생성하세요.

## 응답 형식 (반드시 JSON으로)
```json
{{
  "start_line": 25,
  "description": "어떤 수정을 했는지 설명",
  "source": "수정된 소스 코드 (복사해서 바로 붙여넣기 가능하게)",
  "notes": "적용 시 주의사항"
}}
```

## 규칙
- start_line: 원본 파일에서 이 소스를 삽입/교체할 시작 라인 번호
- source: 순수 소스 코드만. 마크다운 코드블록 없이.
- 이전 단계에서 사용한 변수명/파라미터명과 반드시 일치시키세요.
- 기존 코드 스타일(들여쓰기, 네이밍)을 유지하세요."""

        user_content = f"""## 원본 요청
{todo['user_request']}

## 현재 작업
파일: {current_item['file_name']}
경로: {current_item['file_path']}
계층: {current_item['layer']}
작업: {current_item['action']}

## 현재 파일 내용
```
{file_content}
```"""

        if prev_summaries:
            user_content += "\n\n## 이전 단계에서 수정한 내용 (변수명/파라미터명 일치 필수)\n"
            for s in prev_summaries:
                user_content += f"- {s}\n"
            # 이전에 제안된 소스도 포함
            for item in todo["items"]:
                if item["order"] < order and item.get("suggested_source"):
                    user_content += (
                        f"\n### {item['file_name']} 수정 소스:\n"
                        f"```\n{item['suggested_source']}\n```\n"
                    )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        result = await self._llm_service._provider.complete(messages)
        parsed = self._extract_json(result)

        if parsed:
            # TODO 항목에 제안 소스 저장
            self.update_todo_item(todo_id, order, {
                "status": "suggested",
                "suggested_source": parsed.get("source", ""),
                "start_line": parsed.get("start_line"),
            })
            # completed_summaries에 추가
            todo = self.get_todo(todo_id)
            if todo:
                todo["completed_summaries"].append({
                    "order": order,
                    "file_name": current_item["file_name"],
                    "action": current_item["action"],
                    "start_line": parsed.get("start_line"),
                })
                self._save_todo(todo_id, todo)

            return parsed

        return {"raw_response": result, "error": "JSON 파싱 실패"}

    async def retry_source(self, todo_id: str, order: int, file_content: str, reason: str = "") -> dict:
        """소스 재생성 (🔄 다시 버튼)"""
        todo = self.get_todo(todo_id)
        if not todo:
            return {"error": "TODO를 찾을 수 없습니다"}

        # 이전 제안 초기화
        self.update_todo_item(todo_id, order, {
            "status": "pending",
            "suggested_source": None,
            "start_line": None,
            "test_status": None,
        })

        return await self.suggest_source(todo_id, order, file_content)

    # ═══════════════════════════════════════════
    # Step 4. 피드백 저장 (👍/👎)
    # ═══════════════════════════════════════════

    def save_feedback(
        self,
        feedback_type: str,
        file_name: str,
        file_path: str = "",
        user_request: str = "",
        reason: str = "",
        suggested_source: str = "",
        start_line: int | None = None,
        todo_id: str = "",
    ) -> dict:
        """피드백 저장 (좋아요/안좋아요)"""
        today = datetime.now().strftime("%Y-%m-%d")
        feedback_file = self._feedback_dir / f"{today}.json"

        feedbacks = []
        if feedback_file.exists():
            feedbacks = json.loads(feedback_file.read_text(encoding="utf-8"))

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": feedback_type,
            "request": user_request,
            "file": file_name,
            "filePath": file_path,
            "startLine": start_line,
            "reason": reason,
            "suggestedSource": suggested_source[:500] if suggested_source else "",
            "todoId": todo_id,
        }
        feedbacks.append(entry)

        feedback_file.write_text(
            json.dumps(feedbacks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"피드백 저장: {feedback_type} — {file_name}")
        return entry

    def get_feedbacks(self, date: str = "", feedback_type: str = "") -> list[dict]:
        """피드백 조회 (날짜/타입 필터)"""
        result = []
        pattern = f"{date}*.json" if date else "*.json"
        for p in sorted(self._feedback_dir.glob(pattern), reverse=True):
            items = json.loads(p.read_text(encoding="utf-8"))
            if feedback_type:
                items = [i for i in items if i.get("type") == feedback_type]
            result.extend(items)
        return result

    # ═══════════════════════════════════════════
    # Step 5. 완료 보고서 & 히스토리
    # ═══════════════════════════════════════════

    async def generate_report(self, todo_id: str) -> dict:
        """TODO 완료 후 보고서 생성"""
        todo = self.get_todo(todo_id)
        if not todo:
            return {"error": "TODO를 찾을 수 없습니다"}

        items_summary = []
        for item in todo["items"]:
            status_icon = {
                "done": "✅",
                "skipped": "⏭️",
                "failed": "❌",
                "suggested": "📝",
                "pending": "⬜",
            }.get(item["status"], "⬜")

            test_info = ""
            if item.get("test_status"):
                test_info = f" [{item['test_status']}]"
            if item.get("test_reason"):
                test_info += f" ({item['test_reason']})"

            line_info = f"L{item['start_line']}~" if item.get("start_line") else ""
            items_summary.append(
                f"  {status_icon} {item['file_name']:30s} {line_info} {item['action']}{test_info}"
            )

        report = {
            "todo_id": todo_id,
            "request": todo["user_request"],
            "summary": todo.get("summary", ""),
            "created_at": todo["created_at"],
            "completed_at": todo.get("completed_at", datetime.now().isoformat()),
            "total_items": len(todo["items"]),
            "done_count": sum(1 for i in todo["items"] if i["status"] == "done"),
            "skipped_count": sum(1 for i in todo["items"] if i["status"] == "skipped"),
            "failed_count": sum(1 for i in todo["items"] if i["status"] == "failed"),
            "items_detail": items_summary,
            "items": todo["items"],
        }

        # 히스토리에 저장
        history_file = self._history_dir / f"{todo_id}.json"
        history = {
            "todo_id": todo_id,
            "request": todo["user_request"],
            "summary": todo.get("summary", ""),
            "created_at": todo["created_at"],
            "completed_at": report["completed_at"],
            "total": report["total_items"],
            "done": report["done_count"],
            "skipped": report["skipped_count"],
            "failed": report["failed_count"],
            "files": [
                {
                    "file_name": i["file_name"],
                    "file_path": i["file_path"],
                    "action": i["action"],
                    "status": i["status"],
                    "start_line": i.get("start_line"),
                }
                for i in todo["items"]
            ],
        }
        history_file.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"작업 히스토리 저장: {todo_id}")

        return report

    def get_history(self, limit: int = 20) -> list[dict]:
        """작업 히스토리 조회 (최근순)"""
        result = []
        for p in sorted(self._history_dir.glob("*.json"), reverse=True)[:limit]:
            result.append(json.loads(p.read_text(encoding="utf-8")))
        return result

    # ═══════════════════════════════════════════
    # 내부 유틸리티
    # ═══════════════════════════════════════════

    def _save_todo(self, todo_id: str, todo: dict) -> None:
        path = self._todo_dir / f"{todo_id}.json"
        path.write_text(
            json.dumps(todo, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """LLM 응답에서 JSON 추출"""
        import re
        # ```json ... ``` 블록 찾기
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        # 전체 텍스트가 JSON인 경우
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        # { ... } 블록 찾기
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None
