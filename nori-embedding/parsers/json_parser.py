"""
수집된 JSON 데이터를 임베딩용 Document로 파싱하는 모듈.

지원 포맷:
  - javadoc: class_name, package_name, constructors, methods, fields
  - spring-doc: title, sections, full_text
  - web-ui / desktop-ui: title, sections, full_text, category
  - community-qa: question_text, answers (StackOverflow)
  - community-issue: question_text, comments (GitHub Issues)
  - database: content (plain text)
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("nori-embedding")


@dataclass
class Document:
    """임베딩 파이프라인 통합 문서 단위"""
    id: str
    text: str
    metadata: dict = field(default_factory=dict)


# ────────────────────────────────────────────────────────────
# Metadata 보강 유틸
# ────────────────────────────────────────────────────────────
DOMAIN_MAP: dict[str, str] = {
    "javadoc":            "dev",
    "spring-doc":         "dev",
    "community":          "dev",
    "community-qa":       "dev",
    "community-tutorial": "dev",
    "community-issue":    "dev",
    "egov":               "dev",
    "database":           "database",
    "database-doc":       "database",
    "oracle-doc":         "database",
    "mariadb-doc":        "database",
    "mongodb-doc":        "database",
    "postgresql-doc":     "database",
    "redis-doc":          "database",
    "sqlite-doc":         "database",
    "web-ui":             "ui",
    "desktop-ui":         "ui",
}

_KO_STOPWORDS = {
    "이", "가", "을", "를", "은", "는", "에", "의", "로", "으로",
    "에서", "와", "과", "도", "만", "이다", "있다", "하다", "된다",
    "합니다", "됩니다", "수", "것", "등", "및", "또는", "그리고",
    "하여", "통해", "위해", "때문에", "경우", "대해", "대한",
    "이렇게", "저렇게", "어떻게",
}
_EN_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "are", "was",
    "has", "have", "not", "but", "can", "will", "all", "one", "its",
    "returns", "method", "class", "object", "type", "value",
}
_IDENTIFIER_RE = re.compile(
    r"\b[A-Z][a-zA-Z0-9]{2,}\b|[a-z][a-z0-9]*(?:[A-Z][a-zA-Z0-9]+)+"
)

# 드의 가중치 설정
_HEADING_WEIGHT = 3   # [Method], [Constructor], # 제목 등
_IDENTIFIER_WEIGHT = 2  # camelCase / PascalCase 식별자
_BODY_WEIGHT = 1        # 하위 콘텐츠 일반 단어


def _detect_domain(source_type: str) -> str:
    return DOMAIN_MAP.get(source_type, "misc")


def _detect_language(text: str) -> str:
    ko = len(re.findall(r"[\uAC00-\uD7A3]", text))
    java_kw = len(re.findall(
        r"\b(?:class|interface|public|private|protected|void|return|import|package|extends|implements)\b",
        text,
    ))
    en = len(re.findall(r"[a-zA-Z]", text))
    if java_kw >= 3:
        return "java"
    if ko > en:
        return "ko"
    return "en"


def _extract_entities(text: str) -> str:
    """Java 식별자 및 대문자 시작 단어 추출. ChromaDB 호환을 위해 쉼표 구분 문자열 반환."""
    matches = _IDENTIFIER_RE.findall(text)
    seen: list[str] = []
    for m in matches:
        if m not in seen:
            seen.append(m)
        if len(seen) >= 15:
            break
    return ",".join(seen)


# ── Heading 가중치 기반 Keyword 추출 ──
_HEADING_RE = re.compile(r"(?:\[(.*?)\]|^#{1,6}\s+(.+))\s+([A-Za-z0-9_]+)", re.M)
_CAMEL_RE = re.compile(r"[a-z][a-z0-9]*(?:[A-Z][a-zA-Z0-9]+)+")


def _extract_keywords_weighted(text: str, top_n: int = 10) -> str:
    """도큐 Heading ·3 / 식별자 ·2 / 도큐 Body ·1 가중치 키워드 추출.

    Returns CSV 문자열 (ChromaDB metadata 호환).
    """
    score: Counter = Counter()

    # 1) Heading 영역 — [Method] substring, # 제목 등
    for m in _HEADING_RE.finditer(text):
        # 그룹 1: 레이블 (간혁표 표기), 그룹 2: markdown heading, 그룹 3: 블록 첫 단어
        label = m.group(1) or m.group(2) or ""
        word = m.group(3)
        # label 자체도 키워드
        for tok in re.findall(r"[a-zA-Z]{3,}", label):
            if tok.lower() not in _EN_STOPWORDS:
                score[tok.lower()] += _HEADING_WEIGHT
        if word and word.lower() not in _EN_STOPWORDS:
            score[word.lower()] += _HEADING_WEIGHT

    # 2) camelCase / PascalCase 식별자 — Identifier weight
    for ident in _CAMEL_RE.findall(text):
        score[ident.lower()] += _IDENTIFIER_WEIGHT
    for pascal in re.findall(r"\b[A-Z][a-z][a-zA-Z0-9]+\b", text):
        score[pascal.lower()] += _IDENTIFIER_WEIGHT

    # 3) Body 일반 단어
    ko_words = re.findall(r"[\uAC00-\uD7A3]{2,}", text)
    en_words = re.findall(r"[a-zA-Z]{3,}", text)
    for w in ko_words:
        if w not in _KO_STOPWORDS:
            score[w] += _BODY_WEIGHT
    for w in en_words:
        lw = w.lower()
        if lw not in _EN_STOPWORDS:
            score[lw] += _BODY_WEIGHT

    return ",".join(w for w, _ in score.most_common(top_n))


def _extract_keywords(text: str) -> str:
    """보조 함수: heading 가중치 방식으로 키워드 추출 (ChromaDB CSV 반환)."""
    return _extract_keywords_weighted(text)


def _enrich_metadata(meta: dict, source_type: str, text: str) -> None:
    """domain / language / keywords / entity_names 필드를 meta dict에 인플레이스 추가."""
    meta["domain"] = _detect_domain(source_type)
    meta["language"] = _detect_language(text)
    meta["keywords"] = _extract_keywords(text)
    meta["entity_names"] = _extract_entities(text)


# ────────────────────────────────────────────────────────────
# JavaDoc 파서 (java.lang.String.json 등)
# ────────────────────────────────────────────────────────────
def _parse_javadoc(data: dict, file_path: str) -> list[Document]:
    pkg = data.get("package_name", "")
    cls = data.get("class_name", "")
    fqcn = f"{pkg}.{cls}" if pkg and cls else cls
    base_id = fqcn or Path(file_path).stem

    docs: list[Document] = []
    meta_base = {
        "source_type": "javadoc",
        "package_name": pkg,
        "class_name": cls,
        "url": data.get("url", ""),
    }

    # 1) 클래스 개요 청크
    sig = data.get("signature", "")
    desc = data.get("description", "")
    overview = f"[Java API] {fqcn}\n\n{sig}\n\n{desc}".strip()
    if overview:
        docs.append(Document(
            id=f"javadoc:{base_id}:overview",
            text=overview,
            metadata={**meta_base, "chunk_type": "overview"},
        ))

    # 2) 생성자들
    for i, ctor in enumerate(data.get("constructors", [])):
        text = f"[Constructor] {fqcn}\n{ctor.get('signature', '')}\n\n{ctor.get('description', '')}".strip()
        docs.append(Document(
            id=f"javadoc:{base_id}:ctor:{i}",
            text=text,
            metadata={**meta_base, "chunk_type": "constructor"},
        ))

    # 3) 메서드들 — 각 메서드를 개별 청크로
    for i, method in enumerate(data.get("methods", [])):
        text = f"[Method] {fqcn}\n{method.get('signature', '')}\n\n{method.get('description', '')}".strip()
        docs.append(Document(
            id=f"javadoc:{base_id}:method:{i}",
            text=text,
            metadata={**meta_base, "chunk_type": "method"},
        ))

    # 4) 필드들
    for i, fld in enumerate(data.get("fields", [])):
        text = f"[Field] {fqcn}\n{fld.get('signature', '')}\n\n{fld.get('description', '')}".strip()
        docs.append(Document(
            id=f"javadoc:{base_id}:field:{i}",
            text=text,
            metadata={**meta_base, "chunk_type": "field"},
        ))

    return docs


# ────────────────────────────────────────────────────────────
# Spring / 웹UI / 데스크탑UI 파서 (섹션 기반)
# ────────────────────────────────────────────────────────────
def _parse_section_doc(data: dict, file_path: str) -> list[Document]:
    source_type = data.get("source_type", "spring-doc")
    source_name = data.get("source_name", "")
    title = data.get("title", Path(file_path).stem)
    url = data.get("url", "")
    category = data.get("category", "")

    stem = Path(file_path).stem
    base_id = f"{source_type}:{stem}"

    meta_base = {
        "source_type": source_type,
        "source_name": source_name,
        "title": title,
        "url": url,
    }
    if category:
        meta_base["category"] = category

    docs: list[Document] = []
    sections = data.get("sections", [])

    if sections:
        for i, sec in enumerate(sections):
            heading = sec.get("heading", "")
            content = sec.get("content", "")
            if not content or len(content.strip()) < 20:
                continue
            text = f"[{source_type}] {title} — {heading}\n\n{content}".strip()
            docs.append(Document(
                id=f"{base_id}:sec:{i}",
                text=text,
                metadata={**meta_base, "chunk_type": "section", "heading": heading},
            ))
    else:
        # 섹션 없으면 full_text → content 순서로 fallback
        full_text = data.get("full_text", "") or data.get("content", "")
        if full_text and len(full_text.strip()) >= 20:
            docs.append(Document(
                id=f"{base_id}:full",
                text=f"[{source_type}] {title}\n\n{full_text}".strip(),
                metadata={**meta_base, "chunk_type": "full"},
            ))

    return docs


# ────────────────────────────────────────────────────────────
# Q&A 파서 (StackOverflow 등)
# ────────────────────────────────────────────────────────────
def _parse_qa_doc(data: dict, file_path: str) -> list[Document]:
    """question_text + answers 구조의 Q&A 문서를 파싱한다."""
    source_type = data.get("source_type", "community-qa")
    source_name = data.get("source_name", "")
    title = data.get("title", Path(file_path).stem)
    url = data.get("url", "")
    category = data.get("category", "")

    stem = Path(file_path).stem
    base_id = f"{source_type}:{stem}"

    meta_base = {
        "source_type": source_type,
        "source_name": source_name,
        "title": title,
        "url": url,
    }
    if category:
        meta_base["category"] = category

    docs: list[Document] = []

    # 질문 텍스트
    question = data.get("question_text", "")
    if question and len(question.strip()) >= 20:
        text = f"[Q&A] {title}\n\n질문:\n{question}".strip()
        docs.append(Document(
            id=f"{base_id}:question",
            text=text,
            metadata={**meta_base, "chunk_type": "question"},
        ))

    # 답변들 — 점수 높은 순으로
    answers = data.get("answers", [])
    answers_sorted = sorted(answers, key=lambda a: a.get("score", 0), reverse=True)
    for i, ans in enumerate(answers_sorted):
        ans_text = ans.get("text", "")
        if not ans_text or len(ans_text.strip()) < 20:
            continue
        score = ans.get("score", 0)
        accepted = " ✓" if ans.get("is_accepted") else ""
        text = f"[Q&A] {title} — 답변 (점수:{score}{accepted})\n\n{ans_text}".strip()
        docs.append(Document(
            id=f"{base_id}:answer:{i}",
            text=text,
            metadata={**meta_base, "chunk_type": "answer", "score": score},
        ))

    return docs


# ────────────────────────────────────────────────────────────
# Issue 파서 (GitHub Issues 등)
# ────────────────────────────────────────────────────────────
def _parse_issue_doc(data: dict, file_path: str) -> list[Document]:
    """question_text + comments 구조의 이슈 문서를 파싱한다."""
    source_type = data.get("source_type", "community-issue")
    source_name = data.get("source_name", "")
    title = data.get("title", Path(file_path).stem)
    url = data.get("url", "")
    category = data.get("category", "")

    stem = Path(file_path).stem
    base_id = f"{source_type}:{stem}"

    meta_base = {
        "source_type": source_type,
        "source_name": source_name,
        "title": title,
        "url": url,
    }
    if category:
        meta_base["category"] = category

    docs: list[Document] = []

    # 이슈 본문
    body = data.get("question_text", "") or data.get("body", "")
    labels = data.get("labels", [])
    label_str = f" [{', '.join(labels)}]" if labels else ""

    if body and len(body.strip()) >= 20:
        text = f"[Issue{label_str}] {title}\n\n{body}".strip()
        docs.append(Document(
            id=f"{base_id}:body",
            text=text,
            metadata={**meta_base, "chunk_type": "issue_body"},
        ))

    # 댓글들
    comments = data.get("comments", [])
    for i, cmt in enumerate(comments):
        cmt_text = cmt.get("text", "")
        if not cmt_text or len(cmt_text.strip()) < 20:
            continue
        user = cmt.get("user", "")
        text = f"[Issue] {title} — 댓글 ({user})\n\n{cmt_text}".strip()
        docs.append(Document(
            id=f"{base_id}:comment:{i}",
            text=text,
            metadata={**meta_base, "chunk_type": "comment"},
        ))

    return docs


# ────────────────────────────────────────────────────────────
# 통합 파서 — 소스 타입별 디스패치
# ────────────────────────────────────────────────────────────
_PARSER_MAP = {
    "javadoc": _parse_javadoc,
    "spring-doc": _parse_section_doc,
    "web-ui": _parse_section_doc,
    "desktop-ui": _parse_section_doc,
    "egov": _parse_section_doc,
    "community": _parse_section_doc,
    "community-qa": _parse_qa_doc,
    "community-tutorial": _parse_section_doc,
    "community-issue": _parse_issue_doc,
    "database": _parse_section_doc,
    "database-doc": _parse_section_doc,
    "oracle-doc": _parse_section_doc,
    "mariadb-doc": _parse_section_doc,
    "mongodb-doc": _parse_section_doc,
    "postgresql-doc": _parse_section_doc,
    "redis-doc": _parse_section_doc,
    "sqlite-doc": _parse_section_doc,
}


def parse_json_file(file_path: str | Path) -> list[Document]:
    """단일 JSON 파일을 파싱하여 Document 리스트를 반환한다."""
    fp = Path(file_path)
    if not fp.exists() or fp.name.startswith("_"):
        return []

    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"JSON 파싱 오류 [{fp}]: {e}")
        return []

    source_type = data.get("source_type", "")
    parser = _PARSER_MAP.get(source_type, _parse_section_doc)
    docs = parser(data, str(fp))
    for doc in docs:
        _enrich_metadata(
            doc.metadata,
            doc.metadata.get("source_type", source_type),
            doc.text,
        )
    return docs


def parse_directory(data_dir: str | Path, source_types: list[str] | None = None) -> list[Document]:
    """
    데이터 디렉토리 전체를 재귀 스캔하여 Document 리스트를 반환한다.
    source_types 지정 시 해당 소스타입만 파싱한다.
    """
    data_path = Path(data_dir)
    if not data_path.is_dir():
        logger.error(f"데이터 디렉토리 없음: {data_path}")
        return []

    all_docs: list[Document] = []
    json_files = sorted(data_path.rglob("*.json"))
    total = len(json_files)
    parsed = 0

    for i, fp in enumerate(json_files):
        if fp.name.startswith("_"):
            continue

        docs = parse_json_file(fp)

        # source_type 필터링 (접두사 매칭: "community" → "community-qa" 등 포함)
        if source_types and docs:
            st = docs[0].metadata.get("source_type", "")
            if not any(st == t or st.startswith(t + "-") for t in source_types):
                continue

        all_docs.extend(docs)
        parsed += 1

        if (i + 1) % 200 == 0:
            logger.info(f"파싱 진행 {i+1}/{total} 파일 — 문서 {len(all_docs)}개 생성")

    logger.info(f"파싱 완료: {parsed}개 파일 → {len(all_docs)}개 문서")
    return all_docs
