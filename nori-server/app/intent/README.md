# 의도 분류기 패키지 (app.intent)

일상 대화·특화 도메인에서 사용자 발화의 의도를 파악하고, AI 프롬프트에 넣을 집중 포인트를 정리하는 모듈입니다.

## 컨셉

- **특화 기능**: 신문기사검색, 개발AI 등 도메인별 전용 의도 분류기
- **일상 대화**: 특화되지 않을 때 일상적인 의도 파악 (정보 질문, 추천, 작업 지시 등)

## 도메인

| 도메인 | 용도 | Intent 예 |
|--------|------|------------|
| `development` | 개발 AI (Nori, Cursor 등) | EXPLAIN, SEARCH, REVIEW, GENERATE |
| `general` | 일상 대화 | INFO_QUESTION, RECOMMEND, TASK_REQUEST |
| `news_search` | 신문 기사 검색 (템플릿) | SEARCH_TOPIC, SEARCH_PERIOD |

## 사용법

### 개발 AI (Nori 서버)

```python
from app.intent import get_classifier
classifier = get_classifier("development")
classifier.set_prompt_loader(llm_service.get_prompt)  # classify-route.txt
result = await classifier.classify(
    "게시판에 댓글 추가해줘",
    llm_complete_fn=lambda msgs, **kw: provider.complete(msgs, **kw),
    profile=project_profile,
    has_code=False,
)
# result.tasks → [{"intent":"SEARCH","detail":"댓글 추가","files":[...]}]
# result.to_json() → search_chat 등에서 사용
```

### 일상 대화

```python
classifier = get_classifier("general")
result = await classifier.classify(
    "오늘 날씨 어때?",
    llm_complete_fn=...,
)
# result.intent → "INFO_QUESTION"
# result.prompt_focus → "날씨 정보를 간단히 알려줌"
# result.to_prompt_context() → AI 시스템 프롬프트에 주입
```

### 새 도메인 추가

1. `app/intent/xxx.py` 에 `IntentClassifier` 상속 클래스 작성
2. `get_system_prompt()`, `build_user_message()`, `parse_response()` 구현
3. `register_classifier("xxx", XxxIntentClassifier())` 등록

```python
# app/intent/__init__.py
from app.intent.news_search import NewsSearchIntentClassifier
register_classifier("news_search", NewsSearchIntentClassifier())
```

## ClassifyResult

- `intent`: 분류된 의도
- `detail`: 구체적 요구사항
- `prompt_focus`: AI가 집중할 포인트 (프롬프트에 넣을 문구)
- `tasks`: 도메인별 구조 (development는 `[{intent, detail, files}]`)
- `to_prompt_context()`: `[의도] ... [요구사항] ... [집중 포인트] ...` 형태 문자열
- `to_json()`: JSON 문자열 (하위 호환)
