"""
신문 기사 검색 의도 분류기 (예시/템플릿)

특화 도메인: 신문 기사 검색
- 검색어 추출, 기간, 주제, 정렬 방식 등 파악
- 다른 AI 기능에서 가져다 쓸 수 있도록 패키지화

사용 예:
  from app.intent import get_classifier, register_classifier
  from app.intent.news_search import NewsSearchIntentClassifier
  register_classifier("news_search", NewsSearchIntentClassifier())
  classifier = get_classifier("news_search")
  result = await classifier.classify("최근 3일 경제 뉴스 검색해줘", llm_complete_fn=...)
"""
from app.intent.base import IntentClassifier, ClassifyResult


class NewsSearchIntentClassifier(IntentClassifier):
    """신문 기사 검색 도메인 의도 분류기"""

    domain = "news_search"
    VALID_INTENTS = {
        "SEARCH_TOPIC",    # 주제/키워드 검색
        "SEARCH_PERIOD",   # 기간별 검색
        "SEARCH_SECTION",  # 섹션별 검색 (경제, 정치 등)
        "TREND",          # 트렌드/이슈 파악
        "CLARIFY",        # 되묻기
    }

    def get_system_prompt(self) -> str:
        return """당신은 신문 기사 검색 의도 분석기입니다.
사용자의 요청에서 검색 의도와 조건을 파악하세요.
JSON만 출력하세요.

[intent]
- SEARCH_TOPIC: 특정 주제/키워드로 기사 검색
- SEARCH_PERIOD: 기간 지정 검색 (오늘, 최근 1주일 등)
- SEARCH_SECTION: 섹션별 검색 (경제, 정치, 사회 등)
- TREND: 트렌드/이슈 파악 요청
- CLARIFY: 검색 조건 불명확

[출력 형식]
{"intent":"...","detail":"요청 요약","keywords":["키워드1","키워드2"],"period":"기간","section":"섹션"}

[예시]
"경제 뉴스 최근 3일" → {"intent":"SEARCH_TOPIC","detail":"경제 분야 최근 3일","keywords":["경제"],"period":"3일","section":"경제"}
"""

    def build_user_message(self, message: str, **kwargs) -> str:
        return f"[사용자 요청]\n{message}"

    def parse_response(self, raw: str, fallback_message: str) -> ClassifyResult:
        obj = self._extract_json(raw)
        if obj:
            intent = (obj.get("intent") or "SEARCH_TOPIC").strip().upper()
            if intent not in self.VALID_INTENTS:
                intent = "SEARCH_TOPIC"
            detail = str(obj.get("detail", fallback_message))[:300]
            focus = f"검색조건: keywords={obj.get('keywords',[])}, period={obj.get('period','')}, section={obj.get('section','')}"
            tasks = [{"intent": intent, "detail": detail, **{k: v for k, v in obj.items() if k not in ("intent", "detail")}}]
            return ClassifyResult(intent, detail, prompt_focus=focus, tasks=tasks, raw=obj)
        extracted = self._extract_intent_regex(raw)
        if extracted:
            return ClassifyResult(extracted, fallback_message[:200], tasks=[], raw={})
        return ClassifyResult("SEARCH_TOPIC", fallback_message, tasks=[], raw={})

    def get_valid_intents(self) -> set[str]:
        return self.VALID_INTENTS
