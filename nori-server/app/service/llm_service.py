"""
LLM 서비스 — 프로바이더를 감싸고 프롬프트 관리 + RAG 연동
모든 AI 기능의 중심 허브
"""
import logging
from pathlib import Path
from typing import AsyncGenerator

from app.config.settings import Settings
from app.service.llm_provider import LlmProvider, OpenAiProvider, OllamaProvider

logger = logging.getLogger("nori-server")


class LlmService:
    """LLM 호출 + 프롬프트 관리 통합 서비스"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._provider = self._create_provider(settings)
        self._prompts: dict[str, str] = {}
        self._memo_service = None
        self._load_prompts(settings.prompts_path)

    def set_memo_service(self, memo_service) -> None:
        """MemoService 연결 — LLM 컨텍스트에 프로젝트 메모 자동 주입"""
        self._memo_service = memo_service
        logger.info("메모 서비스 연결 완료")

    def _create_provider(self, s: Settings) -> LlmProvider:
        if s.ai_provider == "ollama":
            return OllamaProvider(
                base_url=s.ollama_base_url,
                model=s.ollama_model,
                temperature=s.ai_temperature,
                max_tokens=s.ai_max_tokens,
            )
        return OpenAiProvider(
            api_key=s.ai_api_key,
            model=s.ai_model,
            temperature=s.ai_temperature,
            max_tokens=s.ai_max_tokens,
        )

    def _load_prompts(self, prompts_dir: Path):
        """prompts/ 폴더에서 프롬프트 템플릿 로드"""
        if not prompts_dir.exists():
            prompts_dir.mkdir(parents=True, exist_ok=True)
            logger.warning(f"프롬프트 디렉토리 생성됨: {prompts_dir}")
            return

        for f in prompts_dir.glob("*.txt"):
            self._prompts[f.stem] = f.read_text(encoding="utf-8")
            logger.info(f"프롬프트 로드: {f.stem}")

    def get_prompt(self, name: str) -> str:
        return self._prompts.get(name, "")

    def _build_messages(self, system_prompt: str, user_content: str,
                        context: str = "") -> list[dict]:
        """시스템 프롬프트 + 메모 컨텍스트 + RAG 컨텍스트 + 사용자 질문으로 메시지 구성"""
        # 모든 응답에 한국어 강제 적용
        korean_rule = "\n\n[필수] 모든 설명과 답변은 반드시 한국어로 작성하세요. 코드 주석도 한국어로 작성합니다."
        messages = [{"role": "system", "content": system_prompt + korean_rule}]

        # 프로젝트 메모 컨텍스트 주입 (DB 스키마, API 매핑, 컨벤션, 에러 로그)
        memo_ctx = self._get_memo_context()
        if memo_ctx:
            messages.append({
                "role": "system",
                "content": f"[프로젝트 메모 — 코드 생성/분석 시 반드시 참조]\n{memo_ctx}",
            })

        if context:
            messages.append({
                "role": "system",
                "content": f"[참고 문서]\n{context}",
            })
        messages.append({"role": "user", "content": user_content})
        return messages

    def _get_memo_context(self) -> str:
        """메모 서비스에서 프로젝트 컨텍스트 가져오기"""
        if self._memo_service is None:
            return ""
        try:
            return self._memo_service.get_context()
        except Exception as e:
            logger.warning(f"메모 컨텍스트 로드 실패: {e}")
            return ""

    # ── 코드 자동완성 ──
    async def complete_code(self, code: str, cursor_context: str,
                            language: str = "java",
                            rag_context: str = "") -> str:
        system = self.get_prompt("code-completion") or (
            f"당신은 {language} 전문 코드 자동완성 AI입니다. "
            f"주어진 코드의 커서 위치 이후에 올 코드를 제안하세요. "
            f"코드만 반환하고, 설명은 필요 없습니다. "
            f"전자정부프레임워크(eGovFrame) 패턴도 숙지하고 있습니다."
        )
        user = f"현재 코드:\n```{language}\n{code}\n```\n\n커서 위치 컨텍스트:\n{cursor_context}"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 코드 생성 ──
    async def generate_code(self, description: str, language: str = "java",
                            project_context: str = "",
                            rag_context: str = "") -> str:
        system = self.get_prompt("code-generate") or (
            f"당신은 {language} 전문 코드 생성 AI입니다. "
            f"사용자의 설명에 맞는 완전한 코드를 생성하세요. "
            f"한국어로 주석을 달아주세요. "
            f"전자정부프레임워크(eGovFrame)의 표준 패턴(Controller→Service→DAO→VO)을 따릅니다."
        )
        user = f"요청: {description}"
        if project_context:
            user += f"\n\n프로젝트 컨텍스트:\n{project_context}"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 코드 설명 ──
    async def explain_code(self, code: str, language: str = "java",
                           rag_context: str = "") -> str:
        system = self.get_prompt("code-explain") or (
            f"당신은 {language} 코드 전문 해설가입니다. 한국어로 설명하세요. "
            f"코드의 목적, 동작 방식, 사용된 패턴, 주의점을 설명합니다. "
            f"초보 개발자도 이해할 수 있게 친절하게 설명하세요."
        )
        user = f"이 코드를 설명해주세요:\n```{language}\n{code}\n```"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 클래스 구조 분석 ──
    async def explain_class(self, code: str, class_info: str = "",
                            rag_context: str = "") -> str:
        system = self.get_prompt("class-explain") or (
            "당신은 Java 클래스 구조 분석 전문가입니다. 한국어로 설명하세요. "
            "클래스의 상속 관계, 인터페이스 구현, 주요 필드와 메서드, "
            "의존성 주입 패턴, 디자인 패턴 사용 여부를 분석합니다."
        )
        user = f"이 클래스를 분석해주세요:\n```java\n{code}\n```"
        if class_info:
            user += f"\n\n추가 정보:\n{class_info}"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 에러 분석 ──
    async def analyze_error(self, error_message: str, code: str = "",
                            stacktrace: str = "",
                            rag_context: str = "") -> str:
        system = self.get_prompt("error-analysis") or (
            "당신은 Java/Spring 에러 분석 전문가입니다. 한국어로 답변하세요. "
            "에러의 원인을 정확히 진단하고, 구체적인 해결 방법을 단계별로 제시합니다. "
            "Oracle DB 에러(ORA-XXXXX), Spring 에러, JPA/Hibernate 에러에 능숙합니다. "
            "전자정부프레임워크(eGov) 환경의 에러도 잘 알고 있습니다. "
            "가능하면 수정된 코드 예시도 함께 제공합니다."
        )
        user = f"에러 메시지:\n{error_message}"
        if stacktrace:
            user += f"\n\n스택트레이스:\n{stacktrace}"
        if code:
            user += f"\n\n관련 코드:\n```java\n{code}\n```"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 에러 자동 수정 ──
    async def fix_error(self, error_message: str, code: str,
                        rag_context: str = "") -> str:
        system = self.get_prompt("error-fix") or (
            "당신은 Java/Spring 코드 자동 수정 AI입니다. 한국어로 답변하세요. "
            "에러 메시지를 분석해서 수정된 코드를 반환하세요. "
            "변경 부분에 한국어 주석으로 설명을 달아주세요. "
            "코드만 반환하고, 마크다운 코드 블록으로 감싸세요."
        )
        user = (f"에러:\n{error_message}\n\n"
                f"수정이 필요한 코드:\n```java\n{code}\n```")
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 코드 리뷰 ──
    async def review_code(self, code: str, language: str = "java",
                          rag_context: str = "") -> str:
        system = self.get_prompt("code-review") or (
            f"당신은 시니어 {language} 코드 리뷰어입니다. 한국어로 리뷰하세요. "
            f"잠재적 버그, 성능 이슈, 보안 취약점, 코드 스타일 문제를 찾아주세요. "
            f"각 이슈에 대해 심각도(🔴 높음/🟡 중간/🟢 낮음)를 표시하고, "
            f"개선된 코드를 제안하세요."
        )
        user = f"이 코드를 리뷰해주세요:\n```{language}\n{code}\n```"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 리팩토링 ──
    async def refactor_code(self, code: str, instruction: str = "",
                            language: str = "java",
                            rag_context: str = "") -> str:
        system = self.get_prompt("code-refactor") or (
            f"당신은 {language} 리팩토링 전문가입니다. 한국어로 답변하세요. "
            f"코드를 더 깔끔하고 효율적으로 개선하세요. "
            f"적용한 리팩토링 기법을 한국어로 설명하세요."
        )
        user = f"리팩토링 대상 코드:\n```{language}\n{code}\n```"
        if instruction:
            user += f"\n\n요청 사항: {instruction}"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 테스트 코드 생성 ──
    async def generate_test(self, code: str, language: str = "java",
                            rag_context: str = "") -> str:
        system = self.get_prompt("test-generate") or (
            f"당신은 {language} 테스트 코드 생성 전문가입니다. 한국어로 답변하세요. "
            f"JUnit 5 기반 단위 테스트를 생성하세요. "
            f"정상 케이스, 예외 케이스, 경계값 테스트를 포함합니다. "
            f"Mockito를 사용해 의존성을 모킹합니다. "
            f"코드 주석은 한국어로 작성합니다."
        )
        user = f"이 코드의 테스트를 생성해주세요:\n```{language}\n{code}\n```"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── JavaDoc 생성 ──
    async def generate_javadoc(self, code: str,
                               rag_context: str = "") -> str:
        system = self.get_prompt("javadoc-generate") or (
            "당신은 JavaDoc 작성 전문가입니다. "
            "클래스, 메서드, 필드에 적절한 JavaDoc 주석을 생성하세요. "
            "한국어로 작성하되, @param, @return, @throws 태그는 영문 태그를 사용합니다. "
            "설명은 한국어로 작성합니다."
        )
        user = f"이 코드에 JavaDoc을 생성해주세요:\n```java\n{code}\n```"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    # ── 디버깅 가이드 ──
    async def suggest_debug(self, error_message: str, code: str = "",
                            rag_context: str = "") -> str:
        system = self.get_prompt("debug-suggest") or (
            "당신은 Java 디버깅 전문가입니다. 한국어로 안내합니다. "
            "브레이크포인트를 어디에 설정해야 하는지, "
            "어떤 변수를 확인해야 하는지, 디버깅 순서를 안내합니다."
        )
        user = f"디버깅 도움이 필요합니다.\n\n에러: {error_message}"
        if code:
            user += f"\n\n코드:\n```java\n{code}\n```"
        messages = self._build_messages(system, user, rag_context)
        return await self._provider.complete(messages)

    async def complete_messages(self, messages: list[dict]) -> str:
        """메시지 리스트를 받아 LLM 1회 호출 (메모 컨텍스트 자동 주입)"""
        memo = self._get_memo_context()
        if memo and messages:
            msgs = [messages[0]]
            msgs.append({"role": "system", "content": f"[프로젝트 메모]\n{memo}"})
            msgs.extend(messages[1:])
            messages = msgs
        return await self._provider.complete(messages)

    async def stream_messages(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """메시지 리스트를 받아 LLM 스트리밍 (메모 컨텍스트 자동 주입)"""
        memo = self._get_memo_context()
        if memo and messages:
            msgs = [messages[0]]
            msgs.append({"role": "system", "content": f"[프로젝트 메모]\n{memo}"})
            msgs.extend(messages[1:])
            messages = msgs
        async for t in self._provider.stream(messages):
            yield t

    # ── 자유 대화 ──
    async def chat(self, question: str, history: list[dict] = None,
                   rag_context: str = "", project_context: str = "") -> str:
        system = self.get_prompt("chat") or (
            "당신은 같은 팀 시니어 Java 개발자입니다.\n"
            "사용자의 요청을 반드시 구현하세요. 거부하지 마세요.\n"
            "변경이 필요한 부분의 코드만 작성하세요. 기존 코드를 그대로 반복하지 마세요.\n"
            "각 파일을 📁 `파일경로` 형식으로 구분하고, 변경 위치에 // ★ 추가 또는 // ★ 수정 주석을 붙이세요.\n"
            "요청하지 않은 코드 정리/리팩토링은 하지 마세요.\n"
            "기술 스택: Java, Spring, eGovFrame, Oracle DB, MyBatis, JSP\n"
        )
        korean_rule = "\n모든 답변은 한국어로 작성하세요."
        messages = [{"role": "system", "content": system + korean_rule}]

        # 프로젝트 메모 컨텍스트 주입
        memo_ctx = self._get_memo_context()
        if memo_ctx:
            messages.append({"role": "system", "content": f"[프로젝트 메모]\n{memo_ctx}"})
        if rag_context:
            messages.append({"role": "system", "content": f"[참고 문서]\n{rag_context}"})
        if history:
            messages.extend(history)

        # ★ 핵심: 소스 코드를 system이 아닌 user 메시지에 질문과 함께 넣기
        # 질문을 먼저, 코드를 나중에 배치 → 7B 모델이 질문에 집중
        user_msg = question
        if project_context:
            _pc = project_context[:16000] if len(project_context) > 16000 else project_context
            user_msg = (
                f"{question}\n\n"
                f"아래는 현재 프로젝트 소스 코드입니다. 이 코드를 참고하여 위 요청에 대한 수정 코드를 작성하세요.\n"
                f"기존 코드를 그대로 반복하지 말고, 변경이 필요한 부분만 보여주세요.\n\n"
                f"{_pc}"
            )
        messages.append({"role": "user", "content": user_msg})
        return await self._provider.complete(messages)

    # ── 파일 선별 (프로필 기반) ──
    async def pick_files(self, question: str, profile: str) -> str:
        system = (
            "당신은 Java 프로젝트 분석 전문가입니다.\n"
            "아래 [프로젝트 프로필]에 이 프로젝트의 실제 파일 목록과 설명이 있습니다.\n"
            "사용자 [질문]에 답하려면 프로필을 분석해서 관련 파일 경로를 선별하세요.\n\n"
            "[분석 절차]\n"
            "1. 프로필에서 '경로:' 뒤에 나오는 실제 경로만 추출 대상으로 삼으세요. 프로필에 없는 경로는 절대 만들지 마세요.\n"
            "2. 질문에서 핵심 키워드를 추출하세요. (기능명, 화면명, 테이블명, 클래스명 등)\n"
            "3. 프로필의 각 파일(경로+제목+AI설명/설명)을 검토하여, 키워드·기능과 관련된 파일을 찾으세요.\n"
            "4. 수정/추가 요청일 때는 처리 흐름에 맞게 전체 스택을 포함하세요:\n"
            "   - VO/DTO → DAO/Mapper XML → Service → Controller → JSP(화면)\n"
            "   - '메일/필드/항목 추가'면 입력 UI(JSP form/list)와 백엔드(VO, DAO, Service, Controller) 모두 포함.\n"
            "5. 도메인 구분: 질문의 대상 기능에 해당하는 파일만 선택하세요. 다른 기능/모듈의 파일은 제외하세요. 복합 요청이면 양쪽 관련 파일 모두 포함.\n"
            "6. 최대 15개. 관련성 높은 순으로 나열하세요.\n\n"
            "[출력 형식]\n"
            "파일 경로만 한 줄에 하나씩 출력하세요. 설명·마크다운·코드블록 없이 경로만.\n"
            "프로필에 관련 파일이 전혀 없을 때만 'NONE' 한 줄만 출력하세요.\n\n"
            "[금지]\n"
            "- 예시나 다른 프로젝트의 경로를 복사하지 마세요. 반드시 주어진 프로필의 '경로:' 값만 사용하세요.\n"
            "- 프로필에 없는 경로를 추측·생성하지 마세요.\n"
        )
        # 프로필이 너무 크면 축약 (32K 모델 컨텍스트 초과 방지)
        pick_profile = self._build_classify_profile(profile) if len(profile) > 15000 else profile
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"[프로젝트 프로필]\n{pick_profile}\n\n[질문]\n{question}"},
        ]
        return await self._provider.complete(messages)

    # ── 스마트 라우팅 (의도 분류 + 태스크 분할 + 파일 선별) ──
    async def classify_and_route(self, question: str, profile: str,
                                 has_code: bool = False) -> str:
        """의도 분류 + 태스크 분할. 프로필/코드 유무에 따라 적절한 분류기 선택."""
        try:
            from app.intent import get_classifier
            # 프로필이나 코드가 있으면 개발 분류기, 없으면 일반 분류기
            domain = "development" if (profile or has_code) else "general"
            classifier = get_classifier(domain)
            if domain == "development":
                classifier.set_prompt_loader(self.get_prompt)
            result = await classifier.classify(
                question,
                llm_complete_fn=lambda msgs, **kw: self._provider.complete(msgs, **kw),
                profile=profile,
                has_code=has_code,
            )
            # general 분류 결과를 development 호환 형식으로 변환
            if domain == "general":
                return self._general_to_dev_json(result)
            return result.to_json()
        except Exception as e:
            import logging
            logging.getLogger("nori-server").error(
                "[classify_and_route] 분류 실패 → QUESTION 폴백: %s", e
            )
            safe_q = (question or "")[:200].replace('"', '\\"')
            return '{"tasks":[{"intent":"QUESTION","detail":"' + safe_q + '","files":[]}]}'

    def _sanitize_classify_response(self, raw: str, fallback_question: str) -> str:
        """하위 호환: 테스트용. app.intent.development.parse_response 위임."""
        from app.intent import get_classifier
        classifier = get_classifier("development")
        result = classifier.parse_response(raw, fallback_question)
        return result.to_json()

    @staticmethod
    def _general_to_dev_json(result) -> str:
        """GeneralIntentClassifier 결과를 development 호환 JSON으로 변환.
        일반 대화 intent를 QUESTION으로 매핑하여 stream_task에서 처리 가능하게."""
        import json
        # general 의도 → development 호환 매핑
        _map = {
            "INFO_QUESTION": "QUESTION",
            "RECOMMEND": "QUESTION",
            "TASK_REQUEST": "QUESTION",
            "EMOTION_SHARE": "QUESTION",
            "SCHEDULE": "QUESTION",
            "CHITCHAT": "CHITCHAT",
            "CLARIFY": "CLARIFY",
        }
        dev_intent = _map.get(result.intent, "QUESTION")
        detail = result.detail or ""
        return json.dumps({
            "tasks": [{"intent": dev_intent, "detail": detail, "files": []}],
            "general_intent": result.intent,
            "prompt_focus": result.prompt_focus,
        })

    def _normalize_classify_tasks(self, obj: dict, fallback_question: str) -> str:
        """하위 호환: 테스트용"""
        from app.intent.development import DevelopmentIntentClassifier
        c = DevelopmentIntentClassifier()
        tasks = obj.get("tasks", [])
        for t in tasks:
            intent = (t.get("intent") or "QUESTION").strip().upper()
            t["intent"] = intent if intent in c.VALID_INTENTS else "QUESTION"
            t["detail"] = str(t.get("detail", fallback_question))[:500]
            t["files"] = [str(f) for f in t.get("files", []) if f][:15]
        import json
        return json.dumps(obj)

    @staticmethod
    def _build_classify_profile(profile: str) -> str:
        """프로필에서 분류에 필요한 핵심 정보만 추출 (기능 요약 + 경로 목록)"""
        if len(profile) <= 15000:
            return profile

        parts = []

        # 1. 기능 요약 섹션 전체 추출 (## 🔍 ~ 다음 ## 전)
        import re
        summary_match = re.search(
            r'(## 🔍 프로젝트 기능 요약.*?)(?=\n## [^🔍]|\n# )',
            profile, re.DOTALL
        )
        if summary_match:
            parts.append(summary_match.group(1).strip())
        else:
            # 기능 요약 없으면 첫 3000자
            parts.append(profile[:3000])

        # 2. 모든 '경로:' 라인 + 바로 다음 '설명:' 라인 추출
        lines = profile.split('\n')
        path_entries = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('경로:'):
                entry = stripped
                # 바로 다음 줄이 '설명:'이면 포함
                if i + 1 < len(lines) and lines[i+1].strip().startswith('설명:'):
                    entry += ' | ' + lines[i+1].strip()
                path_entries.append(entry)

        if path_entries:
            parts.append('\n[파일 경로 목록]')
            parts.append('\n'.join(path_entries))

        result = '\n\n'.join(parts)
        if len(result) > 20000:
            result = result[:20000] + '\n...(이하 생략)'
        return result

    # ── 프로필 기능 요약 ──
    async def describe_file(self, code: str, filename: str = "") -> str:
        """소스코드를 분석하여 1~2줄 비즈니스 설명 생성"""
        # 파일 타입별 프롬프트 분기
        lower = filename.lower()
        if lower.endswith(".xml"):
            lang_hint = "xml"
            system = (
                "이 XML 파일이 어떤 업무를 처리하는지 1~2문장으로 설명하세요. "
                "MyBatis면 어떤 테이블의 어떤 CRUD를 담당하는지 구체적으로 쓰세요. "
                "예: '회원 포인트 테이블(TB_POINT)의 적립/차감/조회 쿼리를 담당. 포인트 이력 조회 시 기간별 페이징 처리 포함.' "
                "한국어로만 작성. 설명만 출력. 마크다운 금지."
            )
        elif lower.endswith(".jsp"):
            lang_hint = "jsp"
            system = (
                "이 JSP 화면이 어떤 업무 화면인지 1~2문장으로 설명하세요. "
                "어떤 데이터를 보여주고, 사용자가 어떤 동작을 할 수 있는지 구체적으로 쓰세요. "
                "예: '게시판 목록 화면. 카테고리별 필터링, 제목/내용 검색, 페이징, 글쓰기 버튼 제공. 목록 클릭 시 상세 페이지로 이동.' "
                "한국어로만 작성. 설명만 출력. 마크다운 금지."
            )
        elif lower.endswith(".sql"):
            lang_hint = "sql"
            system = (
                "이 SQL 파일이 어떤 테이블/업무를 다루는지 1~2문장으로 설명하세요. "
                "테이블명과 주요 컬럼, 비즈니스 용도를 구체적으로 쓰세요. "
                "한국어로만 작성. 설명만 출력. 마크다운 금지."
            )
        elif lower.endswith(".css"):
            lang_hint = "css"
            system = (
                "이 CSS 파일이 어떤 화면/컴포넌트의 스타일을 담당하는지 1~2문장으로 설명하세요. "
                "주요 셀렉터명, 레이아웃 방식, 어떤 UI 요소를 꾸미는지 구체적으로 쓰세요. "
                "예: '상품 목록 페이지의 그리드 레이아웃, 가격 표시 영역, 장바구니 버튼 스타일을 담당. 반응형 디자인 적용.' "
                "한국어로만 작성. 설명만 출력. 마크다운 금지."
            )
        elif lower.endswith(".js"):
            lang_hint = "javascript"
            system = (
                "이 JavaScript 파일이 어떤 화면/기능의 동작을 담당하는지 1~2문장으로 설명하세요. "
                "AJAX 호출 대상 URL, 이벤트 처리, DOM 조작 등을 구체적으로 쓰세요. "
                "예: '게시판 목록의 검색/필터/페이징 AJAX 처리. /board/list.do로 비동기 요청 후 테이블 갱신. 삭제 버튼 확인 대화상자 포함.' "
                "한국어로만 작성. 설명만 출력. 마크다운 금지."
            )
        else:
            lang_hint = "java"
            system = (
                "이 Java 클래스가 어떤 업무를 처리하는지 1~2문장으로 설명하세요. "
                "예: 'Controller면 어떤 URL을 받아서 어떤 처리를 하는지, "
                "Service면 어떤 비즈니스 로직을 수행하는지, DAO면 어떤 테이블을 조작하는지' 구체적으로 쓰세요. "
                "'~를 처리하는 클래스입니다' 같은 동어반복 금지. 실제 업무 내용을 쓰세요. "
                "한국어로만 작성. 설명만 출력. 마크다운 금지."
            )
        # 큰 파일은 스마트 청킹으로 구조 전체를 균등하게 보여줌
        from app.service.file_chunker import smart_chunk_file
        chunked_code = smart_chunk_file(
            file_path=filename,
            content=code,
            query="",  # 쿼리 없이 전체 구조 균등 노출
            max_chars=8000,
        )
        user = f"파일명: {filename}\n\n```{lang_hint}\n{chunked_code}\n```"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return await self._provider.complete(messages)

    async def summarize_profile(self, profile_content: str) -> str:
        system = (
            "개발팀 신규 투입 인원에게 프로젝트를 인수인계하듯 요약하세요.\n\n"
            "[출력 형식]\n"
            "## 🔍 프로젝트 기능 요약\n\n"
            "### 프로젝트 개요\n"
            "이 프로젝트가 뭐하는 서비스인지 한 줄로. 기술 스택 한 줄로. (형식 예: '프로젝트명 — 핵심 업무 한 줄. Spring+eGovFrame+MyBatis+Oracle+JSP')\n\n"
            "### 비즈니스 기능 목록\n"
            "#### 1. 기능명\n"
            "- **URL**: 주요 진입점 URL (Controller의 @RequestMapping)\n"
            "- **화면**: JSP 파일명 나열\n"
            "- **처리 흐름**: 프로필의 실제 클래스명.메서드명() 사용 (예: XxxController.xxx() → XxxServiceImpl.xxx() → mapper.xml#id)\n"
            "- **테이블**: 관련 DB 테이블명 (SQL에서 확인 가능한 것)\n"
            "- **핵심 동작**: 이 기능이 실제로 하는 일 1~2줄 (추상적 표현 금지)\n\n"
            "### 공통/유틸\n"
            "인터셉터, 필터, 유틸 클래스를 한 줄씩\n\n"
            "[작성 규칙]\n"
            "1. 모든 파일 설명을 빠짐없이 읽고 비즈니스 기능별로 묶으세요.\n"
            "2. '~를 관리합니다' 같은 동어반복 금지. 실제로 뭘 하는지 구체적으로 쓰세요.\n"
            "3. 처리 흐름에서 실제 클래스명.메서드명()을 반드시 사용하세요.\n"
            "4. 테이블명이 SQL/Mapper에 보이면 반드시 언급하세요.\n"
            "5. JSP 파일은 해당 기능의 '화면'에 빠짐없이 포함하세요.\n"
            "6. 중요도 순으로 정렬하세요.\n"
            "7. 한국어로만 작성.\n"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": profile_content},
        ]
        return await self._provider.complete(messages)

    # ── 스트리밍 대화 ──
    async def stream_chat(self, question: str, history: list[dict] = None,
                          rag_context: str = "") -> AsyncGenerator[str, None]:
        system = self.get_prompt("chat") or (
            "당신은 같은 팀 시니어 Java 개발자입니다. 동료에게 설명하듯 구체적으로 답변하세요.\n"
            "추상적/교과서적 설명 금지. 실제 클래스명, 메서드명, URL, SQL ID를 직접 언급하세요.\n"
            "기술 스택: Java, Spring, eGovFrame, Oracle DB, MyBatis, JSP\n"
        )
        korean_rule = "\n\n[필수] 모든 설명과 답변은 반드시 한국어로 작성하세요. 코드 주석도 한국어로 작성합니다."
        messages = [{"role": "system", "content": system + korean_rule}]

        # 프로젝트 메모 컨텍스트 주입
        memo_ctx = self._get_memo_context()
        if memo_ctx:
            messages.append({"role": "system", "content": f"[프로젝트 메모]\n{memo_ctx}"})

        if rag_context:
            messages.append({"role": "system", "content": f"[참고 문서]\n{rag_context}"})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": question})
        async for token in self._provider.stream(messages):
            yield token

    # ── 스트리밍 태스크 실행 (스마트 라우팅용) ──
    async def stream_task(self, intent: str, detail: str, code: str = "",
                          rag_context: str = "", file_ctx: str = "",
                          history: list[dict] = None,
                          profile: str = "",
                          entities_cheatsheet: str = "") -> AsyncGenerator[str, None]:
        """의도별 태스크를 스트리밍으로 실행 — 각 intent에 맞는 프롬프트 사용"""
        import logging
        _log = logging.getLogger("nori-server")
        _log.info("[stream_task] intent=%s, detail=%s, file_ctx=%d자, code=%d자, profile=%d자",
                  intent, detail[:100], len(file_ctx), len(code), len(profile))

        # CHITCHAT / CHITCHAT_WEATHER / CHITCHAT_TIME → 일상 대화
        if intent in ("CHITCHAT", "CHITCHAT_WEATHER", "CHITCHAT_TIME"):
            from datetime import datetime
            now = datetime.now()
            time_info = f"현재 시각: {now.strftime('%Y년 %m월 %d일 %A %H시 %M분')}"
            weekday_kr = ['월요일','화요일','수요일','목요일','금요일','토요일','일요일']
            time_info = time_info.replace(now.strftime('%A'), weekday_kr[now.weekday()])

            if intent == "CHITCHAT_WEATHER":
                system_chat = (
                    "당신은 '볼트'라는 이름의 상냥한 시니어 개발자입니다. "
                    "10년차 베테랑답게 여유있고 유머러스하게 대화하세요.\n\n"
                    f"{time_info}\n\n"
                    "규칙:\n"
                    "1. 반말로 친근하게 답변하세요.\n"
                    "2. 실시간 날씨 데이터는 없지만, 계절/시기에 맞는 일반적인 날씨 이야기를 해주세요.\n"
                    "3. '정확한 날씨는 기상청 앱에서 확인해봐~' 같은 안내를 자연스럽게 넣으세요.\n"
                    "4. 날씨에 맞는 옷차림이나 음식 추천을 곁들이면 좋아요.\n"
                    "5. 짧고 자연스럽게 (2~4문장). 이모지 적당히.\n"
                    "6. 개발자스러운 비유나 농담을 자연스럽게 섞으세요 (예: '오늘 날씨 null 아님? 데이터 없어서 모르겠다 ㅋㅋ').\n"
                    "7. 한국어로 답변하세요.\n"
                )
            elif intent == "CHITCHAT_TIME":
                system_chat = (
                    "당신은 '볼트'라는 이름의 상냥한 시니어 개발자입니다. "
                    "10년차 베테랑답게 여유있고 유머러스하게 대화하세요.\n\n"
                    f"{time_info}\n\n"
                    "규칙:\n"
                    "1. 반말로 친근하게 답변하세요.\n"
                    "2. 위 시각 정보를 기반으로 정확하게 알려주세요.\n"
                    "3. 시간대에 맞는 개발자스러운 한마디를 곁들이세요 (아침이면 'git pull 좋은아침~', 저녁이면 'git push하고 퇴근이다~' 등).\n"
                    "4. 짧고 자연스럽게 (1~2문장). 이모지 적당히.\n"
                    "5. 한국어로 답변하세요.\n"
                )
            else:
                system_chat = (
                    "당신은 '볼트'라는 이름의 상냥한 시니어 개발자입니다. "
                    "10년차 베테랑답게 여유있고 유머러스하게 대화하세요. "
                    "후배 개발자를 따뜻하게 챙겨주는 선배 같은 느낌으로.\n\n"
                    f"{time_info}\n\n"
                    "규칙:\n"
                    "1. 반말로 친근하게 답변하세요 (존댓말 금지).\n"
                    "2. 개발 이야기에 얽매이지 마세요. 가벼운 잡담 위주로.\n"
                    "3. 짧고 자연스럽게 (1~3문장). 이모지를 적당히 섞어주세요.\n"
                    "4. 상대방의 감정에 공감하고, 위로·응원을 적절히 사용하세요.\n"
                    "5. 인사에는 인사로, 안부에는 안부로, 감사에는 화답으로 응하세요.\n"
                    "6. 개발자스러운 드립이나 농담을 자연스럽게 섞으세요 "
                    "(예: 'NullPointerException 같은 하루네~', '오늘은 버그 없는 하루 되길 ㅋㅋ', "
                    "'커피는 개발자의 연료지~', 'deploy 전 커피 한잔은 국룰이야').\n"
                    "7. 한국어로 답변하세요.\n"
                )
            messages = [{"role": "system", "content": system_chat}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": detail})
            async for token in self._provider.stream(messages):
                yield token
            return

        # CLARIFY → 사용자에게 되물어보기
        if intent == "CLARIFY":
            yield detail
            return

        # QUESTION/SEARCH → 채팅 스타일
        if intent in ("QUESTION", "SEARCH"):
            # 수정 요청인지 정보 질문인지 구분
            import re
            modify_keywords = re.compile(
                r'추가|수정|변경|삭제|생성|구현|만들어|넣어|바꿔|고쳐|적용|작성|개발')
            is_modify = bool(modify_keywords.search(detail))

            if is_modify:
                system = (
                    "당신은 Plan+ReAct(Reason+Act) 방식으로 동작하는 Java 개발 AI, '볼트'입니다.\n\n"
                    "━━━ 필수 출력 포맷 (반드시 준수) ━━━\n"
                    "답변 시 반드시 아래 구조로 시작하세요:\n\n"
                    "[Todo List]\n"
                    "🟢 (완료) {이전에 완료된 작업 — 첫 응답이면 생략}\n"
                    "🔵 (진행중) {현재 수정할 파일명 및 핵심 작업 1줄 요약}\n"
                    "⚪ (대기) {다음 연관 작업 예고 — 없으면 생략}\n\n"
                    "**Thought**: {전체 목표와 수정 방향을 2~4문장으로 분석. 수정 대상 메서드·클래스 명시}\n"
                    "**Action**:\n"
                    "```java\n"
                    "// ★ 수정된 코드\n"
                    "```\n\n"
                    "━━━ 작업 규칙 ━━━\n"
                    "1. 절대로 '변경할 필요 없습니다' 거부 금지 — 반드시 코드 작성.\n"
                    "2. 코드 출력 후 사용자 피드백을 기다릴 것 (한 번에 하나의 🔵 작업만).\n"
                    "3. 제공된 코드에 없는 메서드/SQL ID 생성 금지.\n"
                    "4. 파일별로 코드를 구분하고 // ★ 추가·수정 주석을 반드시 달 것.\n"
                )
            else:
                system = (
                    "당신은 같은 팀 시니어 Java 개발자입니다.\n"
                    "사용자의 질문에 프로젝트 소스 코드를 기반으로 구체적이고 정확하게 답변하세요.\n\n"
                    "규칙:\n"
                    "1. 실제 클래스명, 메서드명, URL 매핑, SQL ID를 직접 언급하여 설명하세요.\n"
                    "2. '~패턴을 사용합니다' 같은 추상적 설명 대신 실제 동작 흐름을 설명하세요.\n"
                    "3. 코드의 비즈니스 로직을 중심으로 설명하세요.\n"
                    "4. 관련 파일 간의 호출 관계(Controller→Service→DAO→SQL)를 설명하세요.\n"
                    "5. 기술 스택: Java, Spring, eGovFrame, Oracle DB, MyBatis, JSP\n"
                    "6. 답변은 반드시 완결되게 마쳐야 합니다. 중간에 끊기지 말고, 끝에 간단한 마무리 멘트를 넣으세요.\n"
                    "   예: \"— 이상입니다.\", \"추가로 궁금한 점 있으면 말씀해 주세요.\", \"이상으로 답변 마쳤습니다.\"\n"
                )
            korean_rule = "\n모든 답변은 한국어로 작성하세요."
            messages = [{"role": "system", "content": system + korean_rule}]
            memo_ctx = self._get_memo_context()
            if memo_ctx:
                messages.append({"role": "system", "content": f"[프로젝트 메모]\n{memo_ctx}"})
            if entities_cheatsheet and is_modify:
                messages.append({"role": "system", "content": entities_cheatsheet[:1500]})
            if profile:
                _pf = self._build_classify_profile(profile) if len(profile) > 8000 else profile
                pf_label = "[프로젝트 구조 — 수정 대상 파일을 📁 `파일경로` 형식으로 안내하세요.]\n" if is_modify \
                    else "[프로젝트 구조]\n"
                messages.append({"role": "system", "content": pf_label + _pf})
            if rag_context:
                _rag = rag_context[:3000] if len(rag_context) > 3000 else rag_context
                messages.append({"role": "system", "content": f"[참고 문서]\n{_rag}"})
            if history:
                messages.extend(history)

            # ★ 핵심: 소스 코드를 user 메시지에 질문과 함께 넣기
            # 질문을 먼저, 코드를 나중에 → 7B 모델이 질문에 집중
            user_msg = detail
            if file_ctx:
                _fc = file_ctx[:16000] if len(file_ctx) > 16000 else file_ctx
                _log.info("[stream_task] file_ctx 첫200자: %s", _fc[:200])
                if is_modify:
                    user_msg = (
                        f"요청: {detail}\n\n"
                        f"아래 [Observation]은 사용자가 보낸 실제 소스 코드입니다.\n"
                        f"**Thought**부터 시작해서 분석하고, **Action**으로 수정 코드를 제시하세요.\n"
                        f"제공된 코드에 없는 메서드/SQL ID 만들지 마세요.\n\n"
                        f"[Observation]\n{_fc}"
                    )
                else:
                    user_msg = (
                        f"질문: {detail}\n\n"
                        f"아래 소스 코드를 참고하여 위 질문에 구체적으로 답변하세요.\n"
                        f"실제 클래스명, 메서드명, 호출 흐름을 기반으로 설명하세요.\n\n"
                        f"{_fc}"
                    )
            elif profile:
                # file_ctx가 없으면 프로필에서 구조 정보를 전달
                _pf2 = profile[:8000] if len(profile) > 8000 else profile
                _log.info("[stream_task] file_ctx 비어있음! profile 기반 응답. profile=%d자", len(profile))
                if is_modify:
                    user_msg = (
                        f"⚠️ 주의: 소스 파일을 직접 읽지 못했습니다. 아래 프로젝트 프로필만 참고할 수 있습니다.\n\n"
                        f"요청: {detail}\n\n"
                        f"[★★★ 절대 규칙]\n"
                        f"1. 실제 소스 코드를 읽지 못했으므로 코드를 작성하지 마세요.\n"
                        f"2. 존재하지 않는 클래스, 메서드, 변수, 테이블을 만들어내지 마세요.\n"
                        f"3. 대신 아래 정보만 제공하세요:\n"
                        f"   - 수정해야 할 파일 목록 (프로필에서 찾은 실제 경로)\n"
                        f"   - 각 파일에서 수정해야 할 부분의 설명\n"
                        f"   - 사용자에게 '해당 파일을 열고 다시 질문해 달라'는 안내\n\n"
                        f"[프로젝트 프로필]\n"
                        f"{_pf2}"
                    )
                else:
                    user_msg = (
                        f"질문: {detail}\n\n"
                        f"아래 프로젝트 구조를 참고하여 위 질문에 구체적으로 답변하세요.\n"
                        f"실제 클래스명, 메서드명, URL 매핑을 기반으로 설명하세요.\n\n"
                        f"{_pf2}"
                    )
            else:
                _log.info("[stream_task] file_ctx도 profile도 없음!")
            messages.append({"role": "user", "content": user_msg})
            async for token in self._provider.stream(messages):
                yield token
            return

        # ── ERROR_ANALYZE: 에러 분석 전문 프롬프트 ──
        if intent == "ERROR_ANALYZE":
            system = self.get_prompt("error-analysis") or (
                "당신은 Java/Spring 에러 분석 전문가입니다. 한국어로 답변하세요. "
                "에러의 원인을 정확히 진단하고, 구체적인 해결 방법을 단계별로 제시합니다. "
                "Oracle DB 에러(ORA-XXXXX), Spring 에러, JPA/Hibernate 에러에 능숙합니다. "
                "전자정부프레임워크(eGov) 환경의 에러도 잘 알고 있습니다. "
                "가능하면 수정된 코드 예시도 함께 제공합니다."
            )
            korean_rule = "\n모든 답변은 한국어로 작성하세요."
            messages = [{"role": "system", "content": system + korean_rule}]
            memo_ctx = self._get_memo_context()
            if memo_ctx:
                messages.append({"role": "system", "content": f"[프로젝트 메모]\n{memo_ctx}"})
            if rag_context:
                messages.append({"role": "system", "content": f"[참고 문서]\n{rag_context[:3000]}"})
            user_msg = f"에러 메시지:\n{detail}"
            if code:
                user_msg += f"\n\n관련 코드:\n```java\n{code[:8000]}\n```"
            if file_ctx:
                user_msg += f"\n\n관련 파일:\n{file_ctx[:8000]}"
            messages.append({"role": "user", "content": user_msg})
            async for token in self._provider.stream(messages):
                yield token
            return

        # ── ERROR_FIX: 에러 자동 수정 ──
        if intent == "ERROR_FIX":
            system = self.get_prompt("error-fix") or (
                "당신은 Java/Spring 코드 자동 수정 AI입니다. 한국어로 답변하세요. "
                "에러 메시지를 분석해서 수정된 코드를 반환하세요. "
                "변경 부분에 한국어 주석으로 설명을 달아주세요."
            )
            korean_rule = "\n모든 답변은 한국어로 작성하세요."
            messages = [{"role": "system", "content": system + korean_rule}]
            if rag_context:
                messages.append({"role": "system", "content": f"[참고 문서]\n{rag_context[:3000]}"})
            user_msg = f"에러:\n{detail}\n\n수정이 필요한 코드:\n```java\n{code[:8000]}\n```"
            if file_ctx:
                user_msg += f"\n\n관련 파일:\n{file_ctx[:8000]}"
            messages.append({"role": "user", "content": user_msg})
            async for token in self._provider.stream(messages):
                yield token
            return

        # ── EXPLAIN / REVIEW / GENERATE / REFACTOR / GENERATE_DOC / GENERATE_TEST ──
        intent_prompts = {
            "EXPLAIN": "코드를 상세히 분석하고 동작 흐름, 비즈니스 로직을 한국어로 설명하세요.",
            "REVIEW": "잠재적 버그, 성능 이슈, 보안 취약점, 개선점을 한국어로 리뷰하세요.",
            "GENERATE": "요청에 맞는 새 코드를 생성하세요. 한국어 주석을 달아주세요.",
            "REFACTOR": "가독성과 유지보수성을 개선한 리팩토링 코드를 제시하세요.",
            "GENERATE_DOC": "JavaDoc 형식의 문서화 주석을 생성하세요.",
            "GENERATE_TEST": "JUnit 테스트 코드를 생성하세요.",
        }
        if intent in intent_prompts:
            system = (
                f"당신은 시니어 Java 개발자입니다. {intent_prompts[intent]}\n"
                "기술 스택: Java, Spring, eGovFrame, Oracle DB, MyBatis, JSP\n"
                "모든 답변은 한국어로 작성하세요."
            )
            messages = [{"role": "system", "content": system}]
            if rag_context:
                messages.append({"role": "system", "content": f"[참고 문서]\n{rag_context[:3000]}"})
            if profile:
                _pf = self._build_classify_profile(profile) if len(profile) > 8000 else profile
                messages.append({"role": "system", "content": f"[프로젝트 구조]\n{_pf}"})
            user_msg = detail
            if code:
                user_msg += f"\n\n```java\n{code[:8000]}\n```"
            if file_ctx:
                user_msg += f"\n\n{file_ctx[:12000]}"
            messages.append({"role": "user", "content": user_msg})
            async for token in self._provider.stream(messages):
                yield token
            return

        # ── 기타 알 수 없는 intent → 일반 채팅 폴백 ──
        _log.warning("[stream_task] 알 수 없는 intent: %s → 일반 채팅 폴백", intent)
        messages = [{"role": "system", "content": "당신은 시니어 Java 개발자입니다. 한국어로 답변하세요."}]
        if profile:
            messages.append({"role": "system", "content": f"[프로젝트 구조]\n{profile[:6000]}"})
        if rag_context:
            messages.append({"role": "system", "content": f"[참고 문서]\n{rag_context[:3000]}"})
        user_msg = detail
        if code:
            user_msg += f"\n\n```java\n{code[:8000]}\n```"
        if file_ctx:
            user_msg += f"\n\n{file_ctx[:12000]}"
        messages.append({"role": "user", "content": user_msg})
        async for token in self._provider.stream(messages):
            yield token
