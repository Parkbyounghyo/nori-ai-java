"""Source structure extraction and embedding chunk helpers."""

import json
import logging
import re
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("nori-server")

SECRET_KEYS = ("password", "secret", "token", "api_key", "apikey", "access_key", "private_key")


def _project_name(file_path: str) -> str:
    normalized = file_path.replace("\\", "/").strip("/")
    if not normalized:
        return "unknown"
    return normalized.split("/", 1)[0]


def _extract_class_name(content: str) -> str:
    class_m = re.search(r"(?:public\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)", content)
    return class_m.group(1) if class_m else "?"


def _normalize_comment(text: str, limit: int = 220) -> str:
    text = re.sub(r"/\*\*?|\*/", " ", text)
    text = re.sub(r"^\s*\*\s?", " ", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def extract_java_structure(file_path: str, content: str) -> list[dict]:
    lines = content.split("\n")
    items = []
    class_name = _extract_class_name(content)
    method_pattern = re.compile(
        r"^\s*(?:public|private|protected)\s+"
        r"(?:(?:static|final|synchronized|abstract)\s+)*"
        r"([\w<>\[\], ?]+?)\s+(\w+)\s*\(([^)]*)\)"
    )

    i = 0
    pending_doc = ""
    pending_line_comment = ""
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("/**"):
            doc_lines = [line]
            j = i + 1
            while j < len(lines) and "*/" not in lines[j]:
                doc_lines.append(lines[j])
                j += 1
            if j < len(lines):
                doc_lines.append(lines[j])
            pending_doc = _normalize_comment("\n".join(doc_lines))
            i = j + 1
            continue

        if stripped.startswith("//"):
            pending_line_comment = stripped[2:].strip()[:160]
            i += 1
            continue

        match = method_pattern.match(line)
        if match and "(" in line and ")" in line:
            return_type = re.sub(r"\s+", " ", match.group(1)).strip()
            method_name = match.group(2)
            params = re.sub(r"\s+", " ", match.group(3)).strip()
            comment = pending_doc or pending_line_comment
            items.append({
                "type": "method",
                "project": _project_name(file_path),
                "file_path": file_path,
                "class_name": class_name,
                "method_name": method_name,
                "signature": f"{return_type} {method_name}({params})".strip(),
                "parameters": params,
                "return_type": return_type,
                "description": comment,
                "name": f"{class_name}.{method_name}()",
                "comment": comment,
                "line": i + 1,
            })
            pending_doc = ""
            pending_line_comment = ""
        elif stripped:
            pending_line_comment = ""

        i += 1

    return items


def extract_xml_structure(file_path: str, content: str) -> list[dict]:
    items = []
    for match in re.finditer(r'<(select|insert|update|delete)\s+id\s*=\s*["\']([\w.-]+)["\']', content):
        tag, sql_id = match.group(1), match.group(2)
        tail = content[match.end():match.end() + 1200]
        table_match = re.search(r"(?:FROM|INTO|UPDATE|JOIN)\s+([\w$#]+)", tail, re.I)
        items.append({
            "type": "sql",
            "project": _project_name(file_path),
            "file_path": file_path,
            "sql_type": tag.upper(),
            "name": sql_id,
            "description": table_match.group(1) if table_match else "",
            "comment": f"{tag.upper()} query",
            "line": 0,
        })

    if items:
        return items

    for match in re.finditer(r"<([\w:-]+)(?:\s+[^>]*)?>", content):
        tag = match.group(1)
        if tag.startswith("?") or tag.startswith("!"):
            continue
        items.append({
            "type": "config",
            "project": _project_name(file_path),
            "file_path": file_path,
            "name": tag,
            "description": "xml-config",
            "comment": "xml element",
            "line": 0,
        })
        if len(items) >= 12:
            break
    return items


def extract_jsp_structure(file_path: str, content: str) -> list[dict]:
    items = []
    for match in re.finditer(r'<form[^>]+action\s*=\s*["\']([^"\']+)["\']', content, re.I):
        items.append({
            "type": "jsp_action",
            "project": _project_name(file_path),
            "file_path": file_path,
            "name": match.group(1),
            "description": "form action",
            "comment": "form action",
            "line": 0,
        })

    for pattern, label in (
        (r"<title[^>]*>(.*?)</title>", "title"),
        (r"<h1[^>]*>(.*?)</h1>", "h1"),
        (r"<h2[^>]*>(.*?)</h2>", "h2"),
        (r"<label[^>]*>(.*?)</label>", "label"),
        (r"<button[^>]*>(.*?)</button>", "button"),
    ):
        for match in re.finditer(pattern, content, re.I | re.S):
            text = re.sub(r"<[^>]+>", " ", match.group(1))
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            items.append({
                "type": "jsp_keyword",
                "project": _project_name(file_path),
                "file_path": file_path,
                "name": text[:120],
                "description": label,
                "comment": label,
                "line": 0,
            })
            if len(items) >= 20:
                break
        if len(items) >= 20:
            break
    return items


def extract_properties_structure(file_path: str, content: str) -> list[dict]:
    items = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if ":" in line and ("=" not in line or line.index(":") < line.index("=")):
            key, value = line.split(":", 1)
        elif "=" in line:
            key, value = line.split("=", 1)
        else:
            continue
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        masked = "****" if any(secret in key.lower() for secret in SECRET_KEYS) else value[:80]
        items.append({
            "type": "config",
            "project": _project_name(file_path),
            "file_path": file_path,
            "name": key,
            "description": masked,
            "comment": "config entry",
            "line": 0,
        })
        if len(items) >= 40:
            break
    return items


def extract_file_structure(file_path: str, content: str) -> list[dict]:
    lower = file_path.lower()
    if lower.endswith(".java"):
        return extract_java_structure(file_path, content)
    if lower.endswith(".xml") or lower.endswith(".sql"):
        return extract_xml_structure(file_path, content)
    if lower.endswith(".jsp"):
        return extract_jsp_structure(file_path, content)
    if lower.endswith(".properties") or lower.endswith(".yml") or lower.endswith(".yaml"):
        return extract_properties_structure(file_path, content)
    return []


# ── 자산 분류 ──
_TEMPLATE_PATH_PATTERNS = (
    "/editor/", "/editors/",
    "/template/", "/templates/",
    "/layout/", "/layouts/",
    "/include/", "/includes/",
    "/common/ui/", "/common/assets/",
    "/sample/", "/samples/",
    "/publish/", "/publishing/",
    "/skin/", "/skins/",
    "/theme/", "/themes/",
)


def classify_asset(file_path: str) -> dict:
    """알라 파일 경로 기반 수집 정체 반환.

    Returns:
        dict with keys: domain, asset_type, index_mode, search_priority
        - index_mode == 'full': 업무 코드, 전체 청킹
        - index_mode == 'reference_only': 에디터/템플릿, 위치 정보만 저장
    """
    normalized = file_path.replace("\\", "/").lower()
    for pattern in _TEMPLATE_PATH_PATTERNS:
        if pattern in normalized:
            if "/editor" in normalized:
                asset_type = "editor_template"
            elif "/include" in normalized or "/common" in normalized:
                asset_type = "shared_include"
            else:
                asset_type = "layout_template"
            return {
                "domain": "template",
                "asset_type": asset_type,
                "index_mode": "reference_only",
                "search_priority": "low",
            }
    return {
        "domain": "project",
        "asset_type": "business_code",
        "index_mode": "full",
        "search_priority": "normal",
    }


def build_reference_item(file_path: str, classification: dict, project_id: str = "") -> dict:
    """reference_only 자산의 경량 구조 아이템 생성 (full 첨킹 없이 위치만 기록)."""
    normalized = file_path.replace("\\", "/")
    file_name = normalized.rsplit("/", 1)[-1]
    return {
        "type": "template_ref",
        "project": normalized.split("/", 1)[0] if "/" in normalized else project_id,
        "file_path": file_path,
        "name": file_name,
        "asset_type": classification["asset_type"],
        "index_mode": "reference_only",
        "description": f"{classification['asset_type']}: {file_name}",
        "comment": "template-reference",
        "line": 0,
    }


def structure_to_chunks(items: list[dict], file_path: str, max_per_chunk: int = 8) -> Iterator[tuple[str, dict]]:
    del max_per_chunk
    for item in items:
        kind = item.get("type", "item")
        name = item.get("name", "")
        desc = (item.get("description") or item.get("comment") or "").strip()

        if kind == "method":
            text = (
                "TYPE\nController/Service Method\n\n"
                f"PROJECT\n{item.get('project', _project_name(file_path))}\n\n"
                f"FILE\n{item.get('file_path', file_path)}\n\n"
                f"CLASS\n{item.get('class_name', '')}\n\n"
                f"METHOD\n{item.get('method_name', '')}()\n\n"
                f"SIGNATURE\n{item.get('signature', '')}\n\n"
                f"PARAMETERS\n{item.get('parameters', '')}\n\n"
                f"RETURN TYPE\n{item.get('return_type', '')}\n\n"
                f"DESCRIPTION\n{desc}"
            )
        elif kind == "sql":
            text = (
                "TYPE\nMapper Query\n\n"
                f"PROJECT\n{item.get('project', _project_name(file_path))}\n\n"
                f"FILE\n{item.get('file_path', file_path)}\n\n"
                f"METHOD\n{name}\n\n"
                f"SQL TYPE\n{item.get('sql_type', '')}\n\n"
                f"DESCRIPTION\n{desc}"
            )
        elif kind.startswith("jsp"):
            text = (
                "TYPE\nJSP Page\n\n"
                f"PROJECT\n{item.get('project', _project_name(file_path))}\n\n"
                f"FILE\n{item.get('file_path', file_path)}\n\n"
                f"KIND\n{kind}\n\n"
                f"KEYWORD\n{name}\n\n"
                f"DESCRIPTION\n{desc}"
            )
        elif kind == "template_ref":
            text = (
                "TYPE\nTemplate Reference\n\n"
                f"PROJECT\n{item.get('project', _project_name(file_path))}\n\n"
                f"FILE\n{item.get('file_path', file_path)}\n\n"
                f"ASSET TYPE\n{item.get('asset_type', 'template')}\n\n"
                f"DESCRIPTION\n{desc}"
            )
        else:
            text = (
                "TYPE\nConfig File\n\n"
                f"PROJECT\n{item.get('project', _project_name(file_path))}\n\n"
                f"FILE\n{item.get('file_path', file_path)}\n\n"
                f"KEY\n{name}\n\n"
                f"VALUE\n{desc}"
            )

        if len(text.strip()) > 20:
            yield text, {"file": item.get("file_path", file_path), "type": kind, "name": name}


# ── Symbol Graph ──────────────────────────────────────────────────────────────

# @RequestMapping / @GetMapping / @PostMapping 등에서 URL 패턴 추출
_MAPPING_RE = re.compile(
    r'@(?:Request|Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']',
    re.I,
)
# 메서드 호출 패턴: this.foo(...) / service.foo(...) / mapper.foo(...) 등
_CALL_RE = re.compile(
    r"\b([a-z][a-zA-Z0-9_]*)\.([a-zA-Z][a-zA-Z0-9_]*)\s*\("
)
# 필드 선언: private UserService userService; / @Autowired ...
_FIELD_DECL_RE = re.compile(
    r"(?:private|protected)\s+(?:final\s+)?([A-Z][a-zA-Z0-9_<>]+)\s+([a-z][a-zA-Z0-9_]*)\s*;"
)


def _extract_calls_from_method(body: str) -> list[str]:
    """메서드 바디에서 obj.method() 형태 호출 추출 (최대 20개)."""
    calls: list[str] = []
    seen: set[str] = set()
    for m in _CALL_RE.finditer(body):
        key = f"{m.group(1)}.{m.group(2)}"
        if key not in seen:
            calls.append(key)
            seen.add(key)
        if len(calls) >= 20:
            break
    return calls


def _extract_field_types(content: str) -> dict[str, str]:
    """필드 선언에서 변수명 → 타입 매핑 (obj → ClassName).

    예: private UserService userService; → {"userService": "UserService"}
    """
    mapping: dict[str, str] = {}
    for m in _FIELD_DECL_RE.finditer(content):
        field_type = m.group(1)   # e.g. "UserService"
        field_name = m.group(2)   # e.g. "userService"
        mapping[field_name] = field_type
    return mapping


def build_symbol_graph(
    file_path: str,
    content: str,
    class_name: str | None = None,
) -> dict:
    """Java 파일 하나에서 Symbol Graph 노드/엣지 생성.

    Returns:
        {
            "file": file_path,
            "class": class_name,
            "nodes": [{"method": "foo", "signature": "...", "line": N}],
            "edges": [{"caller": "ClassName.foo", "callee_raw": "service.bar"}],
            "field_types": {"service": "UserService"},
            "url_mappings": [{"url": "/user/list", "method": "list"}],
        }
    """
    if not class_name:
        class_name = _extract_class_name(content)

    field_types = _extract_field_types(content)
    nodes: list[dict] = []
    edges: list[dict] = []
    url_mappings: list[dict] = []
    lines = content.split("\n")

    method_pattern = re.compile(
        r"^\s*(?:public|private|protected)\s+"
        r"(?:(?:static|final|synchronized|abstract)\s+)*"
        r"([\w<>\[\], ?]+?)\s+(\w+)\s*\(([^)]*)\)"
    )

    i = 0
    pending_doc = ""
    pending_urls: list[str] = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # URL 매핑 어노테이션 수집
        for url_m in _MAPPING_RE.finditer(line):
            pending_urls.append(url_m.group(1))

        # JavaDoc 스킵
        if stripped.startswith("/**"):
            j = i + 1
            while j < len(lines) and "*/" not in lines[j]:
                j += 1
            i = j + 1
            continue

        match = method_pattern.match(line)
        if match and "(" in line and ")" in line:
            method_name = match.group(2)
            sig = f"{match.group(1)} {method_name}({match.group(3).strip()})".strip()

            # 메서드 바디 수집 (중괄호 카운팅)
            depth = 0
            body_lines: list[str] = []
            j = i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                body_lines.append(lines[j])
                j += 1
                if depth <= 0 and depth != 0 or (depth == 0 and j > i + 1):
                    break
                if j - i > 300:  # 최대 300줄
                    break
            body = "\n".join(body_lines)

            nodes.append({
                "method": method_name,
                "signature": sig,
                "line": i + 1,
            })

            # URL 매핑 등록
            for url in pending_urls:
                url_mappings.append({"url": url, "method": method_name})
            pending_urls = []

            # 호출 엣지 추출
            for raw_call in _extract_calls_from_method(body):
                obj, callee = raw_call.split(".", 1)
                callee_class = field_types.get(obj, obj)
                edges.append({
                    "caller": f"{class_name}.{method_name}",
                    "callee_raw": raw_call,
                    "callee_class": callee_class,
                    "callee_method": callee,
                })

        i += 1

    return {
        "file": file_path,
        "class": class_name,
        "nodes": nodes,
        "edges": edges,
        "field_types": field_types,
        "url_mappings": url_mappings,
    }


def merge_symbol_graphs(graphs: list[dict]) -> dict:
    """여러 파일의 그래프를 하나의 프로젝트 전체 그래프로 병합.

    Returns:
        {
            "classes": { "ClassName": {"file": ..., "nodes": [...]} },
            "edges": [ {caller, callee_class, callee_method} × N ],
            "urls": { "/url/path": "ClassName.methodName" }
        }
    """
    classes: dict[str, dict] = {}
    all_edges: list[dict] = []
    urls: dict[str, str] = {}

    for g in graphs:
        cls = g["class"]
        if cls:
            classes[cls] = {"file": g["file"], "nodes": g["nodes"]}
        all_edges.extend(g["edges"])
        for um in g["url_mappings"]:
            urls[um["url"]] = f"{cls}.{um['method']}" if cls else um["method"]

    return {"classes": classes, "edges": all_edges, "urls": urls}


def resolve_call_chain(
    start: str,
    merged_graph: dict,
    max_depth: int = 5,
) -> list[str]:
    """start 메서드(ClassName.method)에서 출발하는 호출 체인 반환.

    Args:
        start: "UserController.getUser" 형태
        merged_graph: merge_symbol_graphs() 반환값
        max_depth: 최대 재귀 깊이

    Returns:
        ["UserController.getUser", "UserService.getUser", "UserMapper.selectUser"]
    """
    chain: list[str] = [start]
    visited: set[str] = {start}
    queue = [start]

    while queue and len(chain) < max_depth + 1:
        current = queue.pop(0)
        current_parts = current.split(".", 1)
        current_class = current_parts[0]
        current_method = current_parts[1] if len(current_parts) > 1 else ""

        for edge in merged_graph["edges"]:
            if edge["caller"] == current:
                callee_class = edge.get("callee_class", "")
                callee_method = edge.get("callee_method", "")
                node = f"{callee_class}.{callee_method}"
                if node not in visited:
                    visited.add(node)
                    chain.append(node)
                    queue.append(node)

    return chain


def save_symbol_graph(graph: dict, save_path: str) -> None:
    """Symbol graph를 JSON 파일로 저장."""
    p = Path(save_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Symbol graph 저장 완료: %s (%d nodes, %d edges)",
                save_path, len(graph.get("nodes", [])), len(graph.get("edges", [])))


def load_symbol_graph(save_path: str) -> dict:
    """저장된 Symbol graph JSON 로드. 파일 없으면 빈 dict 반환."""
    p = Path(save_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Symbol graph 로드 실패 (%s): %s", save_path, e)
        return {}

