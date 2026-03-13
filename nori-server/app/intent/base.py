"""
의도 분류기 추상 베이스 — 모든 도메인 분류기의 공통 인터페이스

사용자의 발화를 분석하여:
  1. 의도(intent) 분류
  2. 구체적 요구사항(detail) 추출
  3. AI 프롬프트에 넣을 집중 포인트(prompt_focus) 정리
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import json
import logging
import re

logger = logging.getLogger("nori-intent")


@dataclass
class ClassifyResult:
    """분류 결과 — AI 프롬프트 구성에 사용"""
    intent: str
    detail: str
    prompt_focus: str = ""
    tasks: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """AI 시스템 프롬프트에 넣을 컨텍스트 문자열"""
        parts = [f"[의도] {self.intent}", f"[요구사항] {self.detail}"]
        if self.prompt_focus:
            parts.append(f"[집중 포인트] {self.prompt_focus}")
        return "\n".join(parts)

    def to_json(self) -> str:
        """JSON 문자열로 직렬화 (하위 호환)"""
        return json.dumps({"tasks": self.tasks or [{"intent": self.intent, "detail": self.detail}]})


class IntentClassifier(ABC):
    """도메인별 의도 분류기 추상 클래스"""

    domain: str = "base"

    @abstractmethod
    def get_system_prompt(self) -> str:
        """LLM에 전달할 시스템 프롬프트 — 의도 정의, 규칙, 예시"""
        pass

    @abstractmethod
    def build_user_message(self, message: str, **kwargs) -> str:
        """사용자 메시지 + 컨텍스트 조합"""
        pass

    @abstractmethod
    def parse_response(self, raw: str, fallback_message: str) -> ClassifyResult:
        """LLM 응답 파싱 → ClassifyResult"""
        pass

    def get_valid_intents(self) -> set[str]:
        """이 분류기에서 사용하는 유효 intent 목록"""
        return set()

    async def classify(
        self,
        message: str,
        llm_complete_fn,
        **context,
    ) -> ClassifyResult:
        """
        의도 분류 실행
        llm_complete_fn: (messages, **kwargs) -> str 비동기 함수
        LLM 호출 실패 시 안전하게 폴백 결과 반환.
        """
        try:
            system = self.get_system_prompt()
            user = self.build_user_message(message, **context)
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            raw = await llm_complete_fn(messages, temperature=0.1)
            return self.parse_response(raw, message)
        except Exception as e:
            logger.error("[%s] classify 실패 (LLM 호출 에러) → 폴백: %s", self.domain, e)
            fallback_intent = self._get_fallback_intent()
            truncated = (message or "")[:200]
            return ClassifyResult(
                intent=fallback_intent,
                detail=truncated,
                prompt_focus="분류 실패로 기본 의도 사용",
                tasks=[{"intent": fallback_intent, "detail": truncated, "files": []}],
                raw={"error": str(e)},
            )

    def _get_fallback_intent(self) -> str:
        """분류 실패 시 사용할 기본 intent — 하위 클래스에서 오버라이드 가능"""
        return "QUESTION"

    # ── 공통 유틸: JSON 파싱/검증 ──

    def _extract_json(self, raw: str) -> Optional[dict]:
        """LLM 응답에서 JSON 객체 추출"""
        raw = (raw or "").strip()
        if not raw:
            return None
        if "```" in raw:
            m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
            if m:
                raw = m.group(1).strip()
        for candidate in [raw, raw.split("\n")[0]]:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        start = raw.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(raw)):
                if raw[i] == "{":
                    depth += 1
                elif raw[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw[start:i + 1])
                        except json.JSONDecodeError:
                            fixed = re.sub(r",\s*}", "}", raw[start:i + 1])
                            fixed = re.sub(r",\s*]", "]", fixed)
                            try:
                                return json.loads(fixed)
                            except json.JSONDecodeError:
                                pass
                        break
        return None

    def _extract_intent_regex(self, raw: str) -> Optional[str]:
        """정규식으로 intent 추출 (파싱 실패 시 fallback)"""
        m = re.search(r'"intent"\s*:\s*"([A-Za-z_가-힣]+)"', raw)
        if m:
            intent = m.group(1).strip().upper().replace(" ", "_")
            if intent in self.get_valid_intents():
                return intent
        return None
