"""
의도 분류기 레지스트리 — 도메인별 분류기 등록 및 조회
"""
from typing import Optional

from app.intent.base import IntentClassifier

_registry: dict[str, IntentClassifier] = {}


def register_classifier(domain: str, classifier: IntentClassifier) -> None:
    """도메인에 분류기 등록"""
    _registry[domain] = classifier


def get_classifier(domain: str) -> Optional[IntentClassifier]:
    """도메인별 분류기 조회. 없으면 None"""
    return _registry.get(domain)


def list_domains() -> list[str]:
    """등록된 도메인 목록"""
    return list(_registry.keys())
