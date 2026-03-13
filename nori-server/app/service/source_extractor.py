"""소스 구조 추출 — 클래스·메서드·변수·주석 (인덱싱용)

전체 소스가 아닌 시그니처+설명만 추출하여 검색 효율 향상.
"""
import re
import logging
from typing import Iterator
from pathlib import Path

logger = logging.getLogger("nori-server")


def extract_java_structure(file_path: str, content: str) -> list[dict]:
    """Java 파일에서 클래스, 메서드, 필드, 주석 추출"""
    lines = content.split("\n")
    items = []
    class_name = None

    # 클래스명 추출
    class_m = re.search(r'(?:public\s+)?(?:abstract\s+)?class\s+(\w+)', content)
    if class_m:
        class_name = class_m.group(1)

    # JavaDoc 블록 추출 (메서드/클래스 위)
    method_pattern = re.compile(
        r'^\s*(?:public|private|protected)\s+'
        r'(?:static\s+)?(?:final\s+)?'
        r'(?:\w+(?:<[^>]+>)?\s+)+\s*(\w+)\s*\('
    )
    field_pattern = re.compile(
        r'^\s*(?:private|protected|public)\s+'
        r'(?:static\s+)?(?:final\s+)?'
        r'(?:\w+(?:<[^>]+>)?(?:\s*\[\s*\])?)\s+(\w+)\s*[;=]'
    )

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # JavaDoc /** ... */
        if stripped.startswith("/**"):
            doc_lines = [stripped]
            j = i + 1
            while j < n and "*/" not in lines[j]:
                doc_lines.append(lines[j].strip())
                j += 1
            if j < n:
                doc_lines.append(lines[j].strip())
            doc_text = " ".join(doc_lines).replace("/**", "").replace("*/", "").replace("*", "").strip()
            doc_text = re.sub(r"\s+", " ", doc_text)[:200]

            # 다음 non-empty 줄이 메서드/필드인지 확인
            k = j + 1
            while k < n and not lines[k].strip():
                k += 1
            if k < n:
                m = method_pattern.match(lines[k])
                if m:
                    method_name = m.group(1)
                    cname = class_name or "?"
                    items.append({
                        "type": "method",
                        "name": f"{cname}.{method_name}()",
                        "comment": doc_text,
                        "line": k + 1,
                    })
                else:
                    f = field_pattern.match(lines[k])
                    if f and doc_text:
                        items.append({
                            "type": "field",
                            "name": f"{class_name or '?'}.{f.group(1)}",
                            "comment": doc_text,
                            "line": k + 1,
                        })
            i = j + 1
            continue

        # 인라인 주석 // 도 메서드와 매칭
        m = method_pattern.match(line)
        if m and not stripped.startswith("//"):
            method_name = m.group(1)
            cname = class_name or "?"
            comment = ""
            if i > 0 and "//" in lines[i - 1]:
                comment = lines[i - 1].split("//", 1)[-1].strip()[:150]
            items.append({
                "type": "method",
                "name": f"{cname}.{method_name}()",
                "comment": comment,
                "line": i + 1,
            })

        i += 1

    return items


def extract_xml_structure(file_path: str, content: str) -> list[dict]:
    """XML(MyBatis)에서 SQL ID, 테이블/컬럼 힌트 추출"""
    items = []
    # SQL ID
    for m in re.finditer(r'<(select|insert|update|delete)\s+id\s*=\s*["\'](\w+)["\']', content):
        tag, sid = m.group(1), m.group(2)
        items.append({"type": "sql", "name": sid, "comment": f"{tag} ID", "line": 0})
    # FROM/INTO/UPDATE 뒤 테이블명
    for m in re.finditer(r'(?:FROM|INTO|UPDATE|JOIN)\s+(\w+)', content, re.I):
        items.append({"type": "table", "name": m.group(1), "comment": "", "line": 0})
    return items


def extract_jsp_structure(file_path: str, content: str) -> list[dict]:
    """JSP에서 form, input, 주요 엘리먼트 추출"""
    items = []
    # form action
    for m in re.finditer(r'<form[^>]+action\s*=\s*["\']([^"\']+)["\']', content, re.I):
        items.append({"type": "form", "name": m.group(1), "comment": "form action", "line": 0})
    # input name
    for m in re.finditer(r'<input[^>]+name\s*=\s*["\']([^"\']+)["\']', content, re.I):
        items.append({"type": "input", "name": m.group(1), "comment": "", "line": 0})
    # id 있는 div/section
    for m in re.finditer(r'<(?:div|section)[^>]+id\s*=\s*["\']([^"\']+)["\']', content, re.I):
        items.append({"type": "block", "name": m.group(1), "comment": "", "line": 0})
    return items


def extract_file_structure(file_path: str, content: str) -> list[dict]:
    """파일 타입별 구조 추출"""
    lower = file_path.lower()
    if lower.endswith(".java"):
        return extract_java_structure(file_path, content)
    if lower.endswith(".xml") or lower.endswith(".sql"):
        return extract_xml_structure(file_path, content)
    if lower.endswith(".jsp"):
        return extract_jsp_structure(file_path, content)
    return []


def structure_to_chunks(items: list[dict], file_path: str, max_per_chunk: int = 8) -> Iterator[tuple[str, dict]]:
    """추출된 구조를 임베딩용 텍스트 청크로 변환"""
    lines = []
    for it in items:
        name = it.get("name", "")
        comment = it.get("comment", "").strip()
        if comment:
            lines.append(f"{name} — {comment}")
        else:
            lines.append(name)

    text = "\n".join(lines)
    if len(text.strip()) < 20:
        return
    yield text, {"file": file_path, "type": "source_structure", "count": len(items)}
