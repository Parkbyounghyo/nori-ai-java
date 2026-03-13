"""
Agent Tool Base — 모든 Tool의 공통 인터페이스

Tool은 Agent Loop에서 LLM이 호출하는 단위 작업이다.
각 Tool은 입력을 받아 결과를 반환하며, Safety Guard를 통과한 경우에만 실행된다.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Tool 실행 결과"""
    tool_name: str
    success: bool
    data: Any = None
    error: str = ""
    metadata: dict = field(default_factory=dict)

    def to_text(self) -> str:
        """LLM 컨텍스트에 넣을 텍스트 표현."""
        if not self.success:
            return f"[{self.tool_name}] ERROR: {self.error}"
        if isinstance(self.data, str):
            return f"[{self.tool_name}]\n{self.data}"
        if isinstance(self.data, list):
            lines = "\n".join(str(item) for item in self.data[:50])
            return f"[{self.tool_name}]\n{lines}"
        return f"[{self.tool_name}]\n{self.data}"


class BaseTool(ABC):
    """Tool 추상 기반 클래스"""

    name: str = "base_tool"
    description: str = ""

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        """Tool 실행. 구체 Tool에서 구현."""
        pass

    def schema(self) -> dict:
        """Tool 스키마 (LLM function-calling 포맷)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self._parameters(),
        }

    def _parameters(self) -> dict:
        """파라미터 스키마 기본 구현 — 서브클래스에서 오버라이드."""
        return {"type": "object", "properties": {}}
