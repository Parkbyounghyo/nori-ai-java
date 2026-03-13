"""
의도 분류기 패키지 — 도메인별 AI용 의도 파악 및 프롬프트 정리

사용 예:
  from app.intent import get_classifier
  classifier = get_classifier("development")
  classifier.set_prompt_loader(llm_service.get_prompt)  # 개발용: 프롬프트 파일 연동
  result = await classifier.classify("게시판에 댓글 추가해줘", profile=profile, llm_complete_fn=...)
  # result.tasks → [{"intent":"SEARCH","detail":"...","files":[...]}]

  # 일상 대화 (특화 기능 없을 때)
  classifier = get_classifier("general")
  result = await classifier.classify("오늘 날씨 어때?", llm_complete_fn=...)
  # result.prompt_focus → AI가 집중할 포인트

도메인:
  - development: 개발 AI (코드 설명, 수정, 생성, 리뷰 등)
  - general: 일상 대화 의도 파악 (정보 질문, 추천, 작업 지시 등)
  - (확장) news_search: 신문 기사 검색 의도
"""
from app.intent.base import IntentClassifier, ClassifyResult
from app.intent.registry import get_classifier, register_classifier, list_domains
from app.intent.development import DevelopmentIntentClassifier
from app.intent.general import GeneralIntentClassifier

# 기본 등록 (프롬프트 로더는 LlmService 등 사용처에서 set_prompt_loader로 주입)
register_classifier("development", DevelopmentIntentClassifier())
register_classifier("general", GeneralIntentClassifier())

__all__ = [
    "IntentClassifier",
    "ClassifyResult",
    "get_classifier",
    "register_classifier",
    "list_domains",
    "DevelopmentIntentClassifier",
    "GeneralIntentClassifier",
]
