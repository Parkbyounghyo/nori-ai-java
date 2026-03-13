"""
Agent Tool 구현체 5종

1. code_search_tool    — 벡터 DB 코드 검색
2. file_read_tool      — 프로젝트 파일 읽기 (경로 제한 적용)
3. symbol_trace_tool   — Symbol Graph 호출 체인 조회
4. dependency_graph_tool — 클래스 간 의존 관계 조회
5. project_profile_tool  — 프로젝트 프로파일 요약 반환
"""
import logging
import re
from pathlib import Path

from app.tools import BaseTool, ToolResult

logger = logging.getLogger("nori-server")

# ── 파일 읽기 허용 확장자 화이트리스트 ──
_ALLOWED_EXTENSIONS = {
    ".java", ".xml", ".sql", ".properties",
    ".yml", ".yaml", ".jsp", ".js", ".html",
    ".json", ".md", ".txt",
}

# ── 경로 이탈 방지용 패턴 ──
_PATH_TRAVERSAL_RE = re.compile(r"\.\.[/\\]|[/\\]\.\.")


class CodeSearchTool(BaseTool):
    """벡터 DB에서 코드 스니펫 검색"""

    name = "code_search_tool"
    description = "프로젝트 코드베이스에서 질문과 관련된 코드 조각을 검색합니다."

    def __init__(self, emb):
        self._emb = emb

    async def run(self, query: str = "", top_k: int = 5,
                  project_id: str | None = None, **_) -> ToolResult:
        if not query.strip():
            return ToolResult(self.name, False, error="query는 비어있을 수 없습니다.")
        try:
            filters = {"project": project_id} if project_id else None
            results = await self._emb.search(
                query=query, top_k=top_k, filters=filters
            )
            lines: list[str] = []
            for r in results:
                meta = r.get("metadata", {})
                fp = (meta.get("file_path") or meta.get("file") or "").replace("\\", "/")
                score = r.get("score", 0.0)
                text = r.get("text", "")[:600]
                lines.append(f"파일: {fp} (score:{score:.3f})\n{text}")
            data = "\n\n---\n\n".join(lines) if lines else "검색 결과 없음"
            return ToolResult(self.name, True, data=data, metadata={"count": len(results)})
        except Exception as e:
            logger.warning("[CodeSearchTool] 오류: %s", e)
            return ToolResult(self.name, False, error=str(e))

    def _parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 쿼리"},
                "top_k": {"type": "integer", "description": "반환 결과 수 (기본 5)"},
                "project_id": {"type": "string", "description": "프로젝트 ID (선택)"},
            },
            "required": ["query"],
        }


class FileReadTool(BaseTool):
    """프로젝트 파일 읽기 (화이트리스트 확장자 + 경로 제한)"""

    name = "file_read_tool"
    description = "프로젝트 내 파일을 읽어 내용을 반환합니다. 허용된 확장자만 읽을 수 있습니다."

    def __init__(self, base_dir: str | Path = "."):
        self._base = Path(base_dir).resolve()

    async def run(self, file_path: str = "", max_chars: int = 8000, **_) -> ToolResult:
        if not file_path.strip():
            return ToolResult(self.name, False, error="file_path를 지정하세요.")

        # 경로 탐색 공격 방지
        if _PATH_TRAVERSAL_RE.search(file_path):
            return ToolResult(self.name, False, error="경로 탐색(../) 은 허용되지 않습니다.")

        p = (self._base / file_path).resolve()

        # base_dir 탈출 방지
        try:
            p.relative_to(self._base)
        except ValueError:
            return ToolResult(self.name, False, error="허용된 프로젝트 디렉토리 밖 접근은 불가합니다.")

        if p.suffix.lower() not in _ALLOWED_EXTENSIONS:
            return ToolResult(self.name, False,
                              error=f"허용되지 않는 파일 형식: {p.suffix}")

        if not p.exists():
            return ToolResult(self.name, False, error=f"파일을 찾을 수 없음: {file_path}")

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            truncated = content[:max_chars]
            note = "\n...(이하 생략)" if len(content) > max_chars else ""
            return ToolResult(self.name, True,
                              data=truncated + note,
                              metadata={"file": str(p), "size": len(content)})
        except Exception as e:
            return ToolResult(self.name, False, error=str(e))

    def _parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "프로젝트 루트 기준 상대 경로"},
                "max_chars": {"type": "integer", "description": "최대 읽기 문자 수 (기본 8000)"},
            },
            "required": ["file_path"],
        }


class SymbolTraceTool(BaseTool):
    """Symbol Graph에서 메서드 호출 체인 조회"""

    name = "symbol_trace_tool"
    description = "클래스.메서드에서 출발하는 호출 체인(Controller→Service→Mapper)을 반환합니다."

    def __init__(self, merged_graph: dict | None = None):
        """merged_graph: source_extractor.merge_symbol_graphs() 반환값"""
        self._graph = merged_graph or {}

    def update_graph(self, merged_graph: dict) -> None:
        self._graph = merged_graph

    async def run(self, start: str = "", max_depth: int = 5, **_) -> ToolResult:
        if not start.strip():
            return ToolResult(self.name, False, error="start(ClassName.method) 를 지정하세요.")
        if not self._graph:
            return ToolResult(self.name, False, error="Symbol graph가 로드되지 않았습니다.")
        try:
            from app.service.source_extractor import resolve_call_chain
            chain = resolve_call_chain(start, self._graph, max_depth=max_depth)
            if not chain:
                return ToolResult(self.name, True, data=f"{start} 에서 호출 체인 없음")
            data = " → ".join(chain)
            return ToolResult(self.name, True, data=data,
                              metadata={"depth": len(chain), "start": start})
        except Exception as e:
            return ToolResult(self.name, False, error=str(e))

    def _parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "시작 메서드 (예: UserController.getUser)"},
                "max_depth": {"type": "integer", "description": "최대 탐색 깊이 (기본 5)"},
            },
            "required": ["start"],
        }


class DependencyGraphTool(BaseTool):
    """Symbol Graph에서 특정 클래스의 의존 관계 조회"""

    name = "dependency_graph_tool"
    description = "특정 클래스가 의존하는 클래스 목록(필드 타입 기반)을 반환합니다."

    def __init__(self, merged_graph: dict | None = None):
        self._graph = merged_graph or {}

    def update_graph(self, merged_graph: dict) -> None:
        self._graph = merged_graph

    async def run(self, class_name: str = "", **_) -> ToolResult:
        if not class_name.strip():
            return ToolResult(self.name, False, error="class_name 을 지정하세요.")
        if not self._graph:
            return ToolResult(self.name, False, error="Symbol graph가 로드되지 않았습니다.")

        classes = self._graph.get("classes", {})
        if class_name not in classes:
            # 대소문자 무시 부분 매칭
            matches = [c for c in classes if class_name.lower() in c.lower()]
            if not matches:
                return ToolResult(self.name, True,
                                  data=f"'{class_name}' 클래스를 Symbol graph에서 찾을 수 없습니다.")
            class_name = matches[0]

        # 해당 클래스가 caller인 엣지에서 의존 클래스 추출
        deps: dict[str, set[str]] = {}
        for edge in self._graph.get("edges", []):
            caller: str = edge.get("caller", "")
            if caller.startswith(class_name + "."):
                callee_class = edge.get("callee_class", "")
                callee_method = edge.get("callee_method", "")
                if callee_class and callee_class != class_name:
                    deps.setdefault(callee_class, set()).add(callee_method)

        if not deps:
            return ToolResult(self.name, True,
                              data=f"{class_name} 의 외부 의존 관계가 없습니다.")

        lines = [f"  {cls}: {', '.join(sorted(methods))}"
                 for cls, methods in sorted(deps.items())]
        data = f"{class_name} 의존 관계:\n" + "\n".join(lines)
        return ToolResult(self.name, True, data=data,
                          metadata={"class": class_name, "dep_count": len(deps)})

    def _parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "class_name": {"type": "string", "description": "분석할 클래스명"},
            },
            "required": ["class_name"],
        }


class ProjectProfileTool(BaseTool):
    """프로젝트 프로파일(프로필) 요약 반환"""

    name = "project_profile_tool"
    description = "저장된 프로젝트 프로파일을 조회하여 파일 구조·기능 요약을 반환합니다."

    def __init__(self, profile_store=None):
        """profile_store: ProfileStore 인스턴스"""
        self._store = profile_store

    async def run(self, project_id: str = "", user_id: str = "default",
                  max_chars: int = 4000, **_) -> ToolResult:
        if not project_id.strip():
            return ToolResult(self.name, False, error="project_id를 지정하세요.")
        if self._store is None:
            return ToolResult(self.name, False, error="ProfileStore가 설정되지 않았습니다.")
        try:
            profile = await self._store.get_profile(user_id=user_id, project_id=project_id)
            if not profile:
                return ToolResult(self.name, True,
                                  data=f"'{project_id}' 프로파일을 찾을 수 없습니다.")
            summary = str(profile)[:max_chars]
            return ToolResult(self.name, True, data=summary,
                              metadata={"project_id": project_id})
        except Exception as e:
            return ToolResult(self.name, False, error=str(e))

    def _parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "프로젝트 ID"},
                "user_id": {"type": "string", "description": "사용자 ID (기본 'default')"},
            },
            "required": ["project_id"],
        }


# ── Tool Registry ──

class ToolRegistry:
    """등록된 Tool 관리 및 by-name 조회."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    async def run(self, name: str, **kwargs) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(name, False, error=f"Tool '{name}' 을 찾을 수 없습니다.")
        return await tool.run(**kwargs)
