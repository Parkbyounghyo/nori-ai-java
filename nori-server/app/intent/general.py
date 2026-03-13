"""
일상 대화 의도 분류기 — 특화 도메인 없을 때 사용

정보 질문, 추천, 작업 지시, 감정 공유 등 일상적 대화에서
사용자 요구를 파악하여 AI 프롬프트에 넣을 집중 포인트를 정리.
"""
from typing import Optional

from app.intent.base import IntentClassifier, ClassifyResult


class GeneralIntentClassifier(IntentClassifier):
    """일상 대화 의도 분류기 — 도메인 비특화"""

    domain = "general"
    VALID_INTENTS = {
        "INFO_QUESTION",   # 정보 질문 (알고 싶다)
        "RECOMMEND",       # 추천 요청
        "TASK_REQUEST",    # 작업/행동 지시
        "EMOTION_SHARE",   # 감정/상황 공유
        "SCHEDULE",        # 일정/시간 관련
        "CHITCHAT",        # 가벼운 대화
        "CLARIFY",         # 의도 불명확 → 되묻기
    }

    def get_system_prompt(self) -> str:
        return """당신은 일상 대화의 의도 분석기입니다.
사용자의 말에서 요구하는 바를 파악하여 intent와 detail로 정리하세요.
응답은 JSON만 출력하세요. 다른 텍스트 없이 JSON만.

[intent 정의]
- INFO_QUESTION: 정보를 알고 싶어함 (질문)
- RECOMMEND: 추천을 요청함
- TASK_REQUEST: 특정 작업/행동을 요청함
- EMOTION_SHARE: 감정, 상황을 공유하거나 공감을 구함
- SCHEDULE: 일정, 시간, 날짜 관련
- CHITCHAT: 인사, 가벼운 대화
- CLARIFY: 의도가 불분명하여 되물어봐야 함

[출력 형식]
{"intent":"INTENT명","detail":"구체적 요구사항(한글)","prompt_focus":"AI가 집중할 포인트"}

[예시]
사용자: "오늘 날씨 어때?"
→ {"intent":"INFO_QUESTION","detail":"오늘 날씨 정보","prompt_focus":"날씨 정보를 간단히 알려줌"}

사용자: "점심 뭐 먹을지 추천해줘"
→ {"intent":"RECOMMEND","detail":"점심 메뉴 추천","prompt_focus":"상황에 맞는 메뉴 2~3개 추천"}

사용자: "이 메일 답장 써줘"
→ {"intent":"TASK_REQUEST","detail":"이메일 답장 작성","prompt_focus":"메일 본문 기반 답장 초안 작성"}

사용자: "오늘 정말 피곤해"
→ {"intent":"EMOTION_SHARE","detail":"피로 호소","prompt_focus":"공감 + 간단한 위로나 조언"}

사용자: "안녕"
→ {"intent":"CHITCHAT","detail":"인사","prompt_focus":"친근한 인사 응답"}
"""

    def build_user_message(self, message: str, **kwargs) -> str:
        context = kwargs.get("context", "")
        if context:
            return f"[추가 컨텍스트]\n{context}\n\n[사용자 발화]\n{message}"
        return f"[사용자 발화]\n{message}"

    def parse_response(self, raw: str, fallback_message: str) -> ClassifyResult:
        import logging
        _log = logging.getLogger("nori-intent")
        obj = self._extract_json(raw)
        if obj:
            intent = (obj.get("intent") or "CHITCHAT").strip().upper().replace(" ", "_")
            if intent not in self.VALID_INTENTS:
                intent = "CHITCHAT"
            detail = str(obj.get("detail", fallback_message))[:300]
            focus = str(obj.get("prompt_focus", ""))[:200]
            return ClassifyResult(
                intent=intent,
                detail=detail,
                prompt_focus=focus or self._intent_to_prompt_focus(intent),
                tasks=[{"intent": intent, "detail": detail, "prompt_focus": focus}],
                raw=obj,
            )
        extracted = self._extract_intent_regex(raw)
        if extracted and extracted in self.VALID_INTENTS:
            _log.info("[general] JSON failed, regex intent=%s", extracted)
            return ClassifyResult(extracted, fallback_message[:200], tasks=[], raw={})
        return ClassifyResult("CHITCHAT", fallback_message, tasks=[], raw={})

    def _intent_to_prompt_focus(self, intent: str) -> str:
        mapping = {
            "INFO_QUESTION": "질문에 맞는 정보를 간결히 제공",
            "RECOMMEND": "상황에 맞는 추천 2~3개 제시",
            "TASK_REQUEST": "요청한 작업 수행 방법 안내",
            "EMOTION_SHARE": "공감과 적절한 응답",
            "SCHEDULE": "일정/시간 관련 정보 제공",
            "CHITCHAT": "친근하고 자연스러운 대화",
            "CLARIFY": "구체적 확인 질문",
        }
        return mapping.get(intent, "자연스러운 대화 응답")

    def get_valid_intents(self) -> set[str]:
        return self.VALID_INTENTS

    def _get_fallback_intent(self) -> str:
        return "CHITCHAT"
