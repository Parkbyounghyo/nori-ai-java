"""
개발 AI 의도 분류기 — 코드 설명, 수정, 생성, 리뷰 등

Nori AI, Cursor 플러그인 등 개발 도우미에서 사용.
프로젝트 프로필 기반 파일 선별 포함.
"""
from pathlib import Path
from typing import Callable, Optional

from app.intent.base import IntentClassifier, ClassifyResult


class DevelopmentIntentClassifier(IntentClassifier):
    """개발 도메인 의도 분류기 — EXPLAIN, SEARCH, REVIEW, GENERATE 등"""

    domain = "development"
    VALID_INTENTS = {
        "EXPLAIN", "REVIEW", "GENERATE", "REFACTOR",
        "ERROR_FIX", "ERROR_ANALYZE", "GENERATE_DOC", "GENERATE_TEST",
        "SEARCH", "QUESTION", "CLARIFY", "CHITCHAT",
        "CHITCHAT_WEATHER", "CHITCHAT_TIME",
    }

    def __init__(self, prompt_loader: Optional[Callable[[str], str]] = None, prompts_dir: Optional[Path] = None):
        self._get_prompt = prompt_loader
        self._prompts_dir = prompts_dir or Path("prompts")

    def set_prompt_loader(self, fn: Callable[[str], str]) -> None:
        """런타임에 프롬프트 로더 주입 (LlmService.get_prompt 등)"""
        self._get_prompt = fn

    def get_system_prompt(self) -> str:
        if self._get_prompt:
            prompt = self._get_prompt("classify-route")
            if prompt and len(prompt.strip()) > 100:
                return prompt
        return self._default_prompt()

    def _default_prompt(self) -> str:
        return (
            "당신은 AI 코딩 어시스턴트의 요청 분석기입니다.\n"
            "사용자 메시지를 분석하여 의도를 분류하세요. JSON만 출력하세요.\n\n"
            "intent: EXPLAIN(코드설명), REVIEW(리뷰), GENERATE(새코드생성), REFACTOR(리팩토링), "
            "ERROR_FIX(에러수정), ERROR_ANALYZE(에러분석), GENERATE_DOC(JavaDoc), "
            "GENERATE_TEST(테스트생성), SEARCH(코드수정/프로젝트질문), QUESTION(일반지식), CLARIFY(되묻기)\n\n"
            "'추가/수정/변경/삭제' → SEARCH. '설명/분석/살펴봐' → EXPLAIN. "
            "프로필 경로만 files에 넣으세요. 최대 10개.\n\n"
            '형식: {"tasks":[{"intent":"...","detail":"...","files":[]}]}'
        )

    def build_user_message(self, message: str, **kwargs) -> str:
        profile = kwargs.get("profile", "")
        has_code = kwargs.get("has_code", False)
        code_hint = (
            "\n\n[★★★ 코드 선택됨] 사용자가 에디터에서 코드를 블록 지정했습니다. "
            "선택된 코드가 타겟이므로 tasks 배열에 1개만 반환하세요. "
            "EXPLAIN/REVIEW/REFACTOR/ERROR_FIX/GENERATE_DOC/GENERATE_TEST 중 하나 선택. "
            "'설명해줘', '해줘봐' 등 모호한 요청은 EXPLAIN으로 분류. files는 [] 빈 배열."
        ) if has_code else ""
        classify_profile = self._build_classify_profile(profile)
        return f"[프로젝트 프로필]\n{classify_profile}{code_hint}\n\n[사용자 메시지]\n{message}"

    def _build_classify_profile(self, profile: str) -> str:
        if len(profile) <= 15000:
            return profile
        import re
        parts = []
        summary_match = re.search(
            r'(## 🔍 프로젝트 기능 요약.*?)(?=\n## [^🔍]|\n# )',
            profile, re.DOTALL
        )
        if summary_match:
            parts.append(summary_match.group(1).strip())
        else:
            parts.append(profile[:3000])
        lines = profile.split("\n")
        path_entries = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("경로:"):
                entry = stripped
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("설명:"):
                    entry += " | " + lines[i + 1].strip()
                path_entries.append(entry)
        if path_entries:
            parts.append("\n[파일 경로 목록]")
            parts.append("\n".join(path_entries))
        result = "\n\n".join(parts)
        if len(result) > 20000:
            result = result[:20000] + "\n...(이하 생략)"
        return result

    def parse_response(self, raw: str, fallback_message: str) -> ClassifyResult:
        import json
        import logging
        _log = logging.getLogger("nori-intent")
        obj = self._extract_json(raw)
        if obj:
            tasks = obj.get("tasks", [])
            if not tasks:
                return ClassifyResult("QUESTION", fallback_message, tasks=[], raw=obj)
            for t in tasks:
                intent = (t.get("intent") or "QUESTION").strip().upper()
                t["intent"] = intent if intent in self.VALID_INTENTS else "QUESTION"
                t["detail"] = str(t.get("detail", fallback_message))[:500]
                t["files"] = [str(f) for f in t.get("files", []) if f][:15]
            first = tasks[0]
            focus = self._intent_to_prompt_focus(first["intent"], first.get("detail", ""))
            return ClassifyResult(
                intent=first["intent"],
                detail=first["detail"],
                prompt_focus=focus,
                tasks=tasks,
                raw=obj,
            )
        extracted = self._extract_intent_regex(raw)
        if extracted:
            _log.info("[development] JSON failed, regex intent=%s", extracted)
            return ClassifyResult(extracted, fallback_message[:200], tasks=[{"intent": extracted, "detail": fallback_message[:200], "files": []}])
        _log.warning("[development] Parse failed, fallback QUESTION")
        return ClassifyResult("QUESTION", fallback_message, tasks=[{"intent": "QUESTION", "detail": fallback_message, "files": []}])

    def _intent_to_prompt_focus(self, intent: str, detail: str) -> str:
        """의도에 따른 AI 프롬프트 집중 포인트"""
        mapping = {
            "EXPLAIN": "선택/지정된 코드의 동작, 구조, 비즈니스 로직을 한국어로 구체적으로 설명",
            "REVIEW": "잠재 버그, 성능 이슈, 보안 취약점, 개선점 검토",
            "SEARCH": "요청한 수정사항을 프로젝트 구조에 맞게 코드로 구현",
            "GENERATE": "새로운 코드/클래스/기능 생성",
            "REFACTOR": "가독성, 유지보수성 개선. 기존 동작 유지",
            "ERROR_FIX": "에러 원인 분석 후 수정 코드 제안",
            "ERROR_ANALYZE": "에러 원인과 해결 방향 분석",
            "GENERATE_DOC": "JavaDoc/주석 생성",
            "GENERATE_TEST": "JUnit 등 테스트 코드 생성",
            "QUESTION": "일반 지식 질문에 답변",
            "CLARIFY": "사용자에게 구체적 요청 확인",
        }
        return mapping.get(intent, detail)

    def get_valid_intents(self) -> set[str]:
        return self.VALID_INTENTS
