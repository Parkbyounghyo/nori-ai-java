"""
Feature Flow Builder — intent 분기 → 구조 검색 → 정형 응답 조립

컨셉변경.md 플로우 구현:
  User Question
   ↓
  Intent Analyzer
   ↓
  Structure Search
   ↓
  Call Graph Trace
   ↓
  Feature Flow Builder  ← 이 모듈
   ↓
  Structured Response
   ↓
  LLM Summary (선택)
"""
import logging
from typing import Any

from app.intent.intent_analyzer import (
    STRUCT_QUERY, FEATURE_LOCATION, FLOW_TRACE,
    DATA_FLOW, PAGE_CONNECTION, IMPACT_ANALYSIS, REVERSE_TRACE,
    analyze_intent_detail,
)
from app.service.structure_search_service import StructureSearchService
from app.service.call_graph_service import CallGraphService

logger = logging.getLogger("nori-server")


class FeatureFlowBuilder:
    """질문 → intent 분기 → 구조 검색 → 정형 응답 조립"""

    def __init__(self):
        self._struct_svc = StructureSearchService()
        self._graph_svc = CallGraphService()

    async def build(self, question: str) -> dict[str, Any]:
        """질문을 받아 분석 → 검색 → 응답 반환"""
        intent_info = analyze_intent_detail(question)
        intent = intent_info["intent"]
        logger.info(f"[FeatureFlowBuilder] intent={intent} matched={intent_info.get('matched')}")

        result: dict[str, Any] = {
            "question": question,
            "intent": intent,
            "intent_matched": intent_info.get("matched"),
            "engine": "structure",
        }

        keyword = _extract_keyword(question)

        if intent == STRUCT_QUERY:
            data = await self._struct_svc.get_project_structure_summary()
            result["structured_response"] = _format_struct_summary(data)
            result["raw"] = data

        elif intent == FEATURE_LOCATION:
            data = await self._struct_svc.search_feature_location(keyword)
            result["structured_response"] = _format_feature_location(data)
            result["raw"] = data

        elif intent == FLOW_TRACE:
            # keyword가 full_name 형태면 method flow 추적, 아니면 feature 흐름
            if "." in keyword:
                data = await self._struct_svc.trace_flow(keyword)
                result["structured_response"] = _format_flow_trace(data)
            else:
                data = await self._graph_svc.get_feature_flow(keyword)
                formatted = self._graph_svc.build_search_response(keyword, data)
                result["structured_response"] = formatted
            result["raw"] = data

        elif intent == DATA_FLOW:
            # URL 패턴이면 data_flow, 아니면 feature_flow
            if keyword.startswith("/") or ".do" in keyword or "/api/" in keyword:
                data = await self._struct_svc.trace_data_flow(keyword)
                result["structured_response"] = _format_data_flow(data)
            else:
                data = await self._graph_svc.get_feature_flow(keyword)
                formatted = self._graph_svc.build_search_response(keyword, data)
                result["structured_response"] = formatted
            result["raw"] = data

        elif intent == PAGE_CONNECTION:
            data = await self._struct_svc.trace_page_connection(keyword)
            result["structured_response"] = _format_page_connection(data)
            result["raw"] = data

        elif intent == IMPACT_ANALYSIS:
            data = await self._struct_svc.analyze_impact(keyword)
            result["structured_response"] = _format_impact_analysis(data)
            result["raw"] = data

        elif intent == REVERSE_TRACE:
            # 테이블명 추출 (TB_ 접두 등)
            table_name = _extract_table_name(question) or keyword
            data = await self._struct_svc.reverse_trace_table(table_name)
            result["structured_response"] = _format_reverse_trace(data)
            result["raw"] = data

        else:
            result["structured_response"] = ""
            result["engine"] = "embedding"  # 임베딩 엔진으로 fallback

        return result


# ────────────────────────────────────────────────
# 포맷 함수
# ────────────────────────────────────────────────

def _format_struct_summary(data: dict) -> str:
    counts = data.get("counts", {})
    class_types = data.get("class_types", [])
    node_types = data.get("node_types", [])

    lines = ["[프로젝트 구조 요약]", ""]
    lines.append("인덱스 현황")
    for k, v in counts.items():
        lines.append(f"  {k}: {v}건")
    lines.append("")

    if class_types:
        lines.append("클래스 유형별")
        for ct in class_types:
            lines.append(f"  {ct['type']}: {ct['count']}개")
        lines.append("")

    if node_types:
        lines.append("그래프 노드 유형별")
        for nt in node_types:
            lines.append(f"  {nt['type']}: {nt['count']}개")

    return "\n".join(lines)


def _format_feature_location(data: dict) -> str:
    keyword = data.get("keyword", "")
    classes = data.get("classes", [])
    methods = data.get("methods", [])
    features = data.get("features", [])

    lines = [f"['{keyword}' 기능 위치 검색 결과]", ""]

    if features:
        lines.append("기능 인덱스")
        for f in features:
            lines.append(f"  {f['feature_name']} — {f.get('description', '')}")
        lines.append("")

    if classes:
        lines.append("관련 클래스")
        for c in classes:
            lines.append(f"  [{c.get('class_type', '?')}] {c['full_name']}")
            lines.append(f"    파일: {c.get('file_path', '')}")
            if c.get("summary"):
                lines.append(f"    설명: {c['summary']}")
        lines.append("")

    if methods:
        lines.append("관련 메서드")
        for m in methods:
            lines.append(f"  {m.get('class_name', '?')}.{m['method_name']}()")
            lines.append(f"    클래스 유형: {m.get('class_type', '')}")
            lines.append(f"    파일: {m.get('file_path', '')}")
            if m.get("summary"):
                lines.append(f"    설명: {m['summary']}")
        lines.append("")

    if not classes and not methods and not features:
        lines.append(f"'{keyword}'에 대한 결과가 없습니다.")
        lines.append("※ 프로젝트 분석기(nori-collector)를 먼저 실행해 구조를 수집해주세요.")

    return "\n".join(lines)


def _format_flow_trace(data: dict) -> str:
    if "error" in data:
        return data["error"]

    start = data.get("start", {})
    flow = data.get("flow", [])

    lines = ["[호출 흐름 추적]", ""]
    lines.append(f"시작: {start.get('full_name', start.get('method_name', '?'))}")
    lines.append("")

    if not flow:
        lines.append("호출 대상 없음")
        return "\n".join(lines)

    lines.append("흐름")
    prev_depth = 0
    for node in flow:
        depth = node.get("depth", 1)
        indent = "  " * depth
        ctype = node.get("class_type", "")
        full = node.get("full_name", node.get("method_name", "?"))
        call_type = node.get("call_type", "CALL")
        if depth > prev_depth:
            lines.append(f"{indent}↓ [{call_type}]")
        lines.append(f"{indent}{full}  ({ctype})")
        prev_depth = depth

    return "\n".join(lines)


def _format_data_flow(data: dict) -> str:
    if "error" in data:
        return data["error"]

    url = data.get("url", "")
    route = data.get("route", {})
    flow = data.get("flow", [])
    tables = data.get("tables", [])

    lines = [f"[데이터 흐름: {url}]", ""]

    if route:
        lines.append(f"URL: {route.get('url', url)}  [{route.get('http_method', '')}]")
        if route.get("class_name"):
            lines.append(f"Controller: {route.get('ctrl_full', route['class_name'])}")
        if route.get("method_name"):
            lines.append(f"처리 메서드: {route.get('method_full', route['method_name'])}")
        lines.append("")

    if flow:
        lines.append("Full Stack 흐름")
        for n in flow:
            edge = n.get("edge_type", "↓")
            lines.append(f"  {edge}")
            lines.append(f"  [{n['node_type']}] {n['node_name']}")
        lines.append("")

    if tables:
        lines.append("관련 DB 테이블")
        for t in tables:
            lines.append(f"  {t.get('relation_type', '')} {t['table_name']}")

    return "\n".join(lines)


def _format_page_connection(data: dict) -> str:
    keyword = data.get("keyword", "")
    pages = data.get("pages", [])

    lines = [f"[화면 연결: '{keyword}']", ""]

    if not pages:
        lines.append("관련 페이지가 없습니다.")
        return "\n".join(lines)

    for p in pages:
        lines.append(f"페이지: {p.get('page_name', p.get('file_path', '?'))}")
        if p.get("url"):
            lines.append(f"  URL: {p['url']}")
        scripts = p.get("scripts", [])
        if scripts:
            lines.append("  JS 파일")
            for s in scripts:
                lines.append(f"    {s.get('file_path', '')}")
        apis = p.get("apis", [])
        if apis:
            lines.append("  API 호출")
            for a in apis:
                lines.append(f"    [{a.get('http_method', '')}] {a.get('api_url', '')}  ({a.get('function_name', '')})")
        lines.append("")

    return "\n".join(lines)


def _format_impact_analysis(data: dict) -> str:
    if "error" in data:
        return data["error"]

    target = data.get("target", {})
    callers = data.get("impacted_callers", [])

    lines = [f"[영향도 분석: {target.get('full_name', '')}]", ""]

    if not callers:
        lines.append("이 메서드를 호출하는 곳이 없습니다.")
        return "\n".join(lines)

    lines.append("영향받는 호출자 목록")
    for c in callers:
        depth = c.get("depth", 1)
        indent = "  " * depth
        lines.append(f"{indent}[depth {depth}] {c.get('full_name', c.get('method_name', '?'))}  ({c.get('class_type', '')})")

    return "\n".join(lines)


def _format_reverse_trace(data: dict) -> str:
    if "error" in data:
        return data["error"]

    table = data.get("table", {})
    sqls = data.get("sqls", [])
    reverse_flow = data.get("reverse_flow", [])

    lines = [f"[역방향 추적: {table.get('table_name', '')}]", ""]

    if table.get("description"):
        lines.append(f"설명: {table['description']}")
        lines.append("")

    if sqls:
        lines.append("이 테이블을 사용하는 SQL/Mapper")
        for s in sqls:
            rt = s.get("relation_type", "")
            lines.append(f"  [{rt}] {s.get('mapper_name', '')} / {s.get('sql_id', '')}")
            if s.get("file_path"):
                lines.append(f"    파일: {s['file_path']}")
        lines.append("")

    if reverse_flow:
        lines.append("역방향 흐름 (Table ← ... ← 화면)")
        for n in reverse_flow:
            edge = n.get("edge_type", "↑")
            lines.append(f"  {edge}")
            lines.append(f"  [{n['node_type']}] {n['node_name']}")

    return "\n".join(lines)


# ────────────────────────────────────────────────
# 키워드 추출 유틸
# ────────────────────────────────────────────────

import re

_STOP_WORDS = {
    "어디", "어느", "어떻게", "알려줘", "보여줘", "추적해줘", "찾아줘",
    "기능", "처리", "흐름", "위치", "파일", "클래스", "메서드",
    "저장", "수정", "삭제", "조회", "검색",
}


def _extract_keyword(question: str) -> str:
    """질문에서 핵심 키워드 추출 (괄호 내 단어 우선, 없으면 명사 추출)"""
    # 괄호 안 단어 우선
    m = re.search(r"['\"`「『【]([^'\"`」』】]+)['\"`」』】]", question)
    if m:
        return m.group(1).strip()

    # 영문 식별자 (클래스명, URL 등)
    m = re.search(r"([A-Z][a-zA-Z]+(?:Controller|Service|Mapper|VO|DTO|Util)?)", question)
    if m:
        return m.group(1)

    # URL 패턴
    m = re.search(r"(/[\w/]+(?:\.do|\.json|\.api)?)", question)
    if m:
        return m.group(1)

    # 한글 명사형 추출 (2자 이상, 불용어 제외)
    words = re.findall(r"[\uAC00-\uD7A3]{2,}", question)
    for w in words:
        if w not in _STOP_WORDS:
            return w

    # 영문 단어
    words = re.findall(r"[a-zA-Z]{3,}", question)
    if words:
        return words[0]

    return question[:20]


def _extract_table_name(question: str) -> str | None:
    """질문에서 테이블명 추출 (TB_ 접두어 패턴)"""
    m = re.search(r"\b(TB_\w+|[A-Z]{2,}_[A-Z_]+)\b", question)
    if m:
        return m.group(1)
    return None
