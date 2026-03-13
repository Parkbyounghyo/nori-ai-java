"""스마트 파일 청킹 — 큰 파일을 논리적 단위로 분할하여 관련 부분만 전달

Java: 메서드/클래스 단위
XML(MyBatis): SQL ID 단위
JSP: 폼/스크립트/테이블 단위

관련 있는 청크만 포함하고, 나머지는 "// ... (Line 50~120, 70줄 생략)" 처리.
"""
import re
import logging

logger = logging.getLogger("nori-server")


def smart_chunk_file(
    file_path: str,
    content: str,
    query: str = "",
    max_chars: int = 8000,
    context_lines: int = 3,
    return_meta: bool = False,
) -> "str | tuple[str, dict]":
    """큰 파일을 스마트하게 청킹하여 관련 부분만 반환

    Args:
        file_path: 파일 경로
        content: 원본 파일 내용
        query: 사용자 요청 (관련 청크 선별용)
        max_chars: 최대 출력 글자수
        context_lines: 생략 경계에 포함할 컨텍스트 줄 수
        return_meta: True이면 (결과문자열, 메타정보dict) 튜플 반환

    Returns:
        라인 번호 포함된 스마트 청킹 결과 (return_meta=True이면 메타정보 포함 튜플)
    """
    lines = content.split('\n')

    # 작은 파일은 그대로 반환
    numbered = _add_line_numbers(lines)
    if len(numbered) <= max_chars:
        if return_meta:
            return numbered, {"total_lines": len(lines), "total_chunks": 1, "selected_chunks": 1, "chunked": False, "start_line": 1}
        return numbered

    fname = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    lower_fname = fname.lower()

    # 파일 타입별 청킹
    if lower_fname.endswith(".java"):
        chunks = _chunk_java(lines)
    elif lower_fname.endswith(".xml") or lower_fname.endswith(".sql"):
        chunks = _chunk_xml(lines)
    elif lower_fname.endswith(".jsp"):
        chunks = _chunk_jsp(lines)
    elif lower_fname.endswith(".css"):
        chunks = _chunk_css(lines)
    elif lower_fname.endswith(".js"):
        chunks = _chunk_js(lines)
    elif lower_fname.endswith(".html") or lower_fname.endswith(".htm"):
        chunks = _chunk_html(lines)
    else:
        chunks = _chunk_generic(lines)

    if not chunks:
        # 청킹 실패 → 앞뒤 균등 분배
        result = _fallback_chunk(lines, max_chars)
        if return_meta:
            return result, {"total_lines": len(lines), "total_chunks": 1, "selected_chunks": 1, "chunked": True}
        return result

    # 쿼리 키워드 추출
    keywords = _extract_keywords(query) if query else set()

    # 각 청크의 관련성 스코어 계산
    scored_chunks = []
    for chunk in chunks:
        score = _score_chunk(chunk, keywords, lines)
        scored_chunks.append((score, chunk))

    # 스코어 순 정렬 (높은 것 먼저)
    scored_chunks.sort(key=lambda x: -x[0])

    # max_chars 내에서 관련 청크 선택
    selected_ranges = set()  # (start, end) 라인 범위
    remaining = max_chars
    header_cost = 50  # 생략 표시 비용

    # 항상 포함: 파일 헤더 (package, import 등) — 첫 번째 청크가 보통 이것
    for score, chunk in scored_chunks:
        if chunk.get("type") == "header":
            selected_ranges.add((chunk["start"], chunk["end"]))
            cost = _estimate_cost(lines, chunk["start"], chunk["end"])
            remaining -= cost
            break

    # 관련성 높은 청크부터 추가
    for score, chunk in scored_chunks:
        r = (chunk["start"], chunk["end"])
        if r in selected_ranges:
            continue
        cost = _estimate_cost(lines, chunk["start"], chunk["end"])
        if remaining - cost - header_cost < 0:
            continue
        selected_ranges.add(r)
        remaining -= cost

    # 선택된 범위를 라인 순서로 정렬
    sorted_ranges = sorted(selected_ranges)

    # 라인 번호 포함하여 출력 생성 (생략 구간 표시)
    result_parts = []
    prev_end = 0

    for start, end in sorted_ranges:
        # 이전 청크와 사이에 생략 구간이 있으면 표시
        gap_start = prev_end
        gap_end = start

        if gap_start < gap_end:
            # 컨텍스트 라인 포함
            ctx_end = min(gap_start + context_lines, gap_end)
            if ctx_end > gap_start:
                for i in range(gap_start, ctx_end):
                    result_parts.append(f"{i+1:4d}| {lines[i]}")

            skip_from = ctx_end
            skip_to = max(gap_end - context_lines, ctx_end)
            if skip_to > skip_from:
                skipped = skip_to - skip_from
                result_parts.append(f"    | // ... (Line {skip_from+1}~{skip_to}, {skipped}줄 생략)")

            if skip_to < gap_end:
                for i in range(skip_to, gap_end):
                    result_parts.append(f"{i+1:4d}| {lines[i]}")

        # 선택된 청크 출력
        for i in range(start, min(end, len(lines))):
            result_parts.append(f"{i+1:4d}| {lines[i]}")

        prev_end = end

    # 마지막 청크 이후 생략
    if prev_end < len(lines):
        ctx_start = prev_end
        ctx_end = min(prev_end + context_lines, len(lines))
        for i in range(ctx_start, ctx_end):
            result_parts.append(f"{i+1:4d}| {lines[i]}")

        if ctx_end < len(lines):
            skipped = len(lines) - ctx_end
            result_parts.append(f"    | // ... (Line {ctx_end+1}~{len(lines)}, {skipped}줄 생략)")

    result = '\n'.join(result_parts)

    logger.info("[스마트청크] %s: %d줄 → %d자 (원본 %d자, 청크 %d/%d개 선택)",
                fname, len(lines), len(result), len(content),
                len(selected_ranges), len(chunks))

    if return_meta:
        start_line = sorted_ranges[0][0] + 1 if sorted_ranges else 1  # 1-based
        return result, {
            "total_lines": len(lines),
            "total_chunks": len(chunks),
            "selected_chunks": len(selected_ranges),
            "chunked": True,
            "start_line": start_line,
        }
    return result


def _add_line_numbers(lines: list) -> str:
    """라인 번호 추가"""
    parts = []
    for i, line in enumerate(lines, 1):
        parts.append(f"{i:4d}| {line}")
    return '\n'.join(parts)


def _estimate_cost(lines: list, start: int, end: int) -> int:
    """라인 범위의 글자수 추정"""
    total = 0
    for i in range(start, min(end, len(lines))):
        total += len(lines[i]) + 7  # "NNNN| " + newline
    return total


def _extract_keywords(query: str) -> set:
    """쿼리에서 검색 키워드 추출"""
    keywords = set()
    # 한글 단어
    for w in re.findall(r'[가-힣]+', query):
        if len(w) >= 2:
            keywords.add(w)
    # 영문 단어 (CamelCase 분리)
    for w in re.findall(r'[A-Za-z]+', query):
        keywords.add(w.lower())
        for sub in re.findall(r'[A-Z][a-z]+|[a-z]+', w):
            if len(sub) > 2:
                keywords.add(sub.lower())
    # 핵심 업무 키워드 매핑
    keyword_map = {
        "메일": ["email", "mail", "smtp"],
        "게시판": ["board", "bbs"],
        "결제": ["pay", "payment", "order"],
        "회원": ["member", "cust", "user"],
        "장바구니": ["cart"],
        "포인트": ["point", "gpoint"],
        "검색": ["search", "find"],
        "삭제": ["delete", "remove", "del"],
        "추가": ["insert", "add", "create"],
        "수정": ["update", "modify", "edit"],
        "조회": ["select", "list", "get"],
        "등록": ["insert", "register", "regist"],
    }
    for k, synonyms in keyword_map.items():
        if k in query:
            keywords.update(synonyms)
    # "파일" → file,upload,download는 업로드/다운로드 문맥에서만. "게시판 관련된 파일"은 소스 목록이므로 제외
    if "파일" in query and any(w in query for w in ("업로드", "다운로드", "첨부", "용량", "제한", "크기")):
        keywords.update(["file", "upload", "download"])

    return keywords


def _score_chunk(chunk: dict, keywords: set, lines: list) -> int:
    """청크의 관련성 스코어 계산"""
    score = 0

    # 청크 타입 기본 점수
    if chunk.get("type") == "header":
        score += 100  # 헤더는 항상 포함
    elif chunk.get("type") == "method":
        score += 5
    elif chunk.get("type") == "sql":
        score += 5

    # 청크 이름과 키워드 매칭
    name = chunk.get("name", "").lower()
    for kw in keywords:
        if kw in name:
            score += 20

    # 청크 내용과 키워드 매칭
    chunk_text = '\n'.join(lines[chunk["start"]:chunk["end"]]).lower()
    for kw in keywords:
        count = chunk_text.count(kw)
        if count > 0:
            score += min(count * 3, 15)

    return score


# ── 파일 타입별 청킹 ──

def _chunk_java(lines: list) -> list:
    """Java 파일을 논리적 단위로 분할"""
    chunks = []
    i = 0
    n = len(lines)

    # 1. 헤더 (package + import)
    header_end = 0
    for j in range(n):
        stripped = lines[j].strip()
        if stripped.startswith("package ") or stripped.startswith("import ") or stripped == "" or stripped.startswith("//"):
            header_end = j + 1
        else:
            break

    if header_end > 0:
        chunks.append({"type": "header", "name": "imports", "start": 0, "end": header_end})

    # 2. 클래스 내 메서드 단위 분할
    # 메서드 시작 패턴: 접근제어자 + 반환타입 + 메서드명(
    method_pattern = re.compile(
        r'^\s*(public|private|protected)\s+.*?\s+(\w+)\s*\('
    )
    # 어노테이션 패턴
    annotation_pattern = re.compile(r'^\s*@\w+')

    method_start = None
    method_name = None
    brace_depth = 0
    annotation_start = None

    for j in range(header_end, n):
        stripped = lines[j].strip()

        # 어노테이션 시작 추적
        if annotation_pattern.match(stripped) and method_start is None:
            if annotation_start is None:
                annotation_start = j

        m = method_pattern.match(lines[j])
        if m and brace_depth <= 1:
            # 이전 메서드 종료
            if method_start is not None:
                chunks.append({"type": "method", "name": method_name, "start": method_start, "end": j})

            method_start = annotation_start if annotation_start is not None else j
            method_name = m.group(2)
            annotation_start = None
        elif not annotation_pattern.match(stripped):
            annotation_start = None

        # 중괄호 추적
        brace_depth += stripped.count('{') - stripped.count('}')

    # 마지막 메서드
    if method_start is not None:
        chunks.append({"type": "method", "name": method_name, "start": method_start, "end": n})

    # 메서드를 못 찾았으면 클래스 본문 전체를 하나로
    if len(chunks) <= 1:
        chunks.append({"type": "body", "name": "class_body", "start": header_end, "end": n})

    return chunks


def _chunk_xml(lines: list) -> list:
    """XML(MyBatis) 파일을 SQL ID 단위로 분할"""
    chunks = []
    n = len(lines)

    # 헤더 (<?xml, <mapper, namespace 등)
    header_end = 0
    for j in range(min(n, 20)):
        if re.search(r'<(mapper|sqlMap|configuration)', lines[j]):
            header_end = j + 1
            break
    if header_end == 0:
        header_end = min(5, n)

    chunks.append({"type": "header", "name": "xml_header", "start": 0, "end": header_end})

    # SQL 블록: <select|insert|update|delete id="..."> ~ </select|...>
    sql_start_pattern = re.compile(r'<(select|insert|update|delete)\s+id\s*=\s*["\'](\w+)')
    sql_end_pattern = re.compile(r'</(select|insert|update|delete)>')

    sql_start = None
    sql_name = None

    for j in range(header_end, n):
        m = sql_start_pattern.search(lines[j])
        if m and sql_start is None:
            sql_start = j
            sql_name = m.group(2)

        if sql_start is not None and sql_end_pattern.search(lines[j]):
            chunks.append({"type": "sql", "name": sql_name, "start": sql_start, "end": j + 1})
            sql_start = None
            sql_name = None

    # 닫히지 않은 SQL
    if sql_start is not None:
        chunks.append({"type": "sql", "name": sql_name, "start": sql_start, "end": n})

    return chunks


def _chunk_jsp(lines: list) -> list:
    """JSP 파일을 논리적 블록으로 분할"""
    chunks = []
    n = len(lines)

    # 헤더 (<%@ 지시어, taglib 등)
    header_end = 0
    for j in range(n):
        stripped = lines[j].strip()
        if stripped.startswith("<%@") or stripped.startswith("<%--") or stripped == "":
            header_end = j + 1
        else:
            break

    if header_end > 0:
        chunks.append({"type": "header", "name": "jsp_header", "start": 0, "end": header_end})

    # <script> 블록
    script_start = None
    for j in range(header_end, n):
        if '<script' in lines[j].lower() and script_start is None:
            script_start = j
        if '</script>' in lines[j].lower() and script_start is not None:
            chunks.append({"type": "script", "name": "javascript", "start": script_start, "end": j + 1})
            script_start = None

    # <form> 블록
    form_start = None
    form_name = ""
    for j in range(header_end, n):
        fm = re.search(r'<form[^>]*(?:id|name)\s*=\s*["\'](\w+)', lines[j], re.IGNORECASE)
        if fm and form_start is None:
            form_start = j
            form_name = fm.group(1)
        if '</form>' in lines[j].lower() and form_start is not None:
            chunks.append({"type": "form", "name": form_name, "start": form_start, "end": j + 1})
            form_start = None

    # <table> 블록
    table_start = None
    for j in range(header_end, n):
        if '<table' in lines[j].lower() and table_start is None:
            table_start = j
        if '</table>' in lines[j].lower() and table_start is not None:
            chunks.append({"type": "table", "name": "table", "start": table_start, "end": j + 1})
            table_start = None

    # 청크가 없으면 전체를 body로
    if len(chunks) <= 1:
        chunks.append({"type": "body", "name": "jsp_body", "start": header_end, "end": n})

    return chunks


def _chunk_css(lines: list) -> list:
    """CSS 파일을 선택자/미디어쿼리 단위로 분할"""
    chunks = []
    n = len(lines)

    # 헤더 (@import, @charset 등)
    header_end = 0
    for j in range(n):
        stripped = lines[j].strip()
        if stripped.startswith("@import") or stripped.startswith("@charset") or stripped == "" or stripped.startswith("/*"):
            header_end = j + 1
        else:
            break
    if header_end > 0:
        chunks.append({"type": "header", "name": "css_header", "start": 0, "end": header_end})

    # 규칙 블록 단위: selector { ... } 또는 @media { ... }
    block_start = None
    block_name = ""
    brace_depth = 0

    for j in range(header_end, n):
        stripped = lines[j].strip()
        if brace_depth == 0 and '{' in stripped and block_start is None:
            block_start = j
            # 선택자 이름 추출
            block_name = stripped.split('{')[0].strip()[:60]

        brace_depth += stripped.count('{') - stripped.count('}')

        if brace_depth <= 0 and block_start is not None:
            chunks.append({"type": "rule", "name": block_name, "start": block_start, "end": j + 1})
            block_start = None
            block_name = ""
            brace_depth = 0

    if block_start is not None:
        chunks.append({"type": "rule", "name": block_name, "start": block_start, "end": n})

    if len(chunks) <= 1:
        chunks.append({"type": "body", "name": "css_body", "start": header_end, "end": n})

    return chunks


def _chunk_js(lines: list) -> list:
    """JavaScript 파일을 함수/이벤트핸들러 단위로 분할"""
    chunks = []
    n = len(lines)

    # 헤더 (import, use strict, 변수 선언 등)
    header_end = 0
    for j in range(n):
        stripped = lines[j].strip()
        if (stripped.startswith("import ") or stripped.startswith("'use strict'")
                or stripped.startswith('"use strict"') or stripped.startswith("//")
                or stripped.startswith("/*") or stripped.startswith("* ") or stripped.startswith("*/")
                or stripped.startswith("var ") or stripped.startswith("let ")
                or stripped.startswith("const ") or stripped == ""):
            header_end = j + 1
        else:
            break
    if header_end > 0:
        chunks.append({"type": "header", "name": "js_header", "start": 0, "end": header_end})

    # 함수 단위 분할
    func_pattern = re.compile(
        r'^\s*(?:(?:export\s+)?(?:async\s+)?function\s+(\w+)|'
        r'(?:var|let|const)\s+(\w+)\s*=\s*(?:async\s+)?function|'
        r'(\w+)\s*[:=]\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>))'
    )
    # jQuery 이벤트 핸들러
    jquery_pattern = re.compile(r'\$\s*\(["\']([^"\']*)["\'\s]\)\s*\.\s*(\w+)\s*\(')
    # $(document).ready / $(function()
    ready_pattern = re.compile(r'\$\s*\(\s*(?:document|function)')

    block_start = None
    block_name = ""
    brace_depth = 0

    for j in range(header_end, n):
        stripped = lines[j].strip()

        if brace_depth == 0:
            fm = func_pattern.match(lines[j])
            jm = jquery_pattern.search(lines[j])
            rm = ready_pattern.search(lines[j])

            if fm or jm or rm:
                if block_start is not None:
                    chunks.append({"type": "function", "name": block_name, "start": block_start, "end": j})

                block_start = j
                if fm:
                    block_name = fm.group(1) or fm.group(2) or fm.group(3) or "anonymous"
                elif jm:
                    block_name = f"{jm.group(1)}.{jm.group(2)}"
                else:
                    block_name = "document.ready"

        brace_depth += stripped.count('{') - stripped.count('}')

        if brace_depth <= 0 and block_start is not None:
            chunks.append({"type": "function", "name": block_name, "start": block_start, "end": j + 1})
            block_start = None
            block_name = ""
            brace_depth = 0

    if block_start is not None:
        chunks.append({"type": "function", "name": block_name, "start": block_start, "end": n})

    if len(chunks) <= 1:
        chunks.append({"type": "body", "name": "js_body", "start": header_end, "end": n})

    return chunks


def _chunk_html(lines: list) -> list:
    """HTML 파일을 주요 블록(head/body/script/style/form) 단위로 분할"""
    chunks = []
    n = len(lines)

    # DOCTYPE + <html> 헤더
    header_end = 0
    for j in range(min(n, 10)):
        stripped = lines[j].strip().lower()
        if stripped.startswith("<!doctype") or stripped.startswith("<html") or stripped == "":
            header_end = j + 1
    if header_end > 0:
        chunks.append({"type": "header", "name": "html_header", "start": 0, "end": header_end})

    # <head> 블록
    head_start = head_end = None
    for j in range(n):
        if '<head' in lines[j].lower() and head_start is None:
            head_start = j
        if '</head>' in lines[j].lower() and head_start is not None:
            head_end = j + 1
            break
    if head_start is not None and head_end is not None:
        chunks.append({"type": "head", "name": "html_head", "start": head_start, "end": head_end})

    # <script> 블록
    script_start = None
    for j in range(n):
        if '<script' in lines[j].lower() and script_start is None:
            script_start = j
        if '</script>' in lines[j].lower() and script_start is not None:
            chunks.append({"type": "script", "name": "javascript", "start": script_start, "end": j + 1})
            script_start = None

    # <style> 블록
    style_start = None
    for j in range(n):
        if '<style' in lines[j].lower() and style_start is None:
            style_start = j
        if '</style>' in lines[j].lower() and style_start is not None:
            chunks.append({"type": "style", "name": "css", "start": style_start, "end": j + 1})
            style_start = None

    # <form> 블록
    form_start = None
    form_name = ""
    for j in range(n):
        fm = re.search(r'<form[^>]*(?:id|name)\s*=\s*["\']([\w-]+)', lines[j], re.IGNORECASE)
        if fm and form_start is None:
            form_start = j
            form_name = fm.group(1)
        if '</form>' in lines[j].lower() and form_start is not None:
            chunks.append({"type": "form", "name": form_name, "start": form_start, "end": j + 1})
            form_start = None

    # <table> 블록
    table_start = None
    for j in range(n):
        if '<table' in lines[j].lower() and table_start is None:
            table_start = j
        if '</table>' in lines[j].lower() and table_start is not None:
            chunks.append({"type": "table", "name": "table", "start": table_start, "end": j + 1})
            table_start = None

    if len(chunks) <= 1:
        chunks.append({"type": "body", "name": "html_body", "start": header_end or 0, "end": n})

    return chunks


def _chunk_generic(lines: list) -> list:
    """일반 파일 — 균등 분할"""
    n = len(lines)
    chunk_size = max(30, n // 5)
    chunks = []
    for i in range(0, n, chunk_size):
        chunks.append({
            "type": "block",
            "name": f"block_{i}",
            "start": i,
            "end": min(i + chunk_size, n),
        })
    return chunks


def _fallback_chunk(lines: list, max_chars: int) -> str:
    """청킹 실패 시 앞뒤 균등 분배"""
    half = max_chars // 2
    head_lines = []
    head_total = 0
    for i, line in enumerate(lines):
        cost = len(line) + 7
        if head_total + cost > half:
            break
        head_lines.append(f"{i+1:4d}| {line}")
        head_total += cost

    tail_lines = []
    tail_total = 0
    for i in range(len(lines) - 1, -1, -1):
        cost = len(lines[i]) + 7
        if tail_total + cost > half:
            break
        tail_lines.insert(0, f"{i+1:4d}| {lines[i]}")
        tail_total += cost

    head_end = len(head_lines)
    tail_start = len(lines) - len(tail_lines) + 1
    skipped = tail_start - head_end - 1

    result = '\n'.join(head_lines)
    if skipped > 0:
        result += f"\n    | // ... (Line {head_end+1}~{tail_start-1}, {skipped}줄 생략)\n"
        result += '\n'.join(tail_lines)

    return result
