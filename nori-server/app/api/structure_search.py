"""
Structure Search API — 프로젝트 구조 기반 검색 엔드포인트

컨셉변경.md Phase 5 검색 엔진 API
  POST /api/v1/structure/search      — 통합 구조 검색 (intent 자동 분기)
  POST /api/v1/structure/flow        — 호출 흐름 추적
  POST /api/v1/structure/data-flow   — 데이터 흐름 (URL → Table)
  POST /api/v1/structure/page        — 화면 연결 (JSP → JS → API)
  POST /api/v1/structure/impact      — 영향도 분석
  POST /api/v1/structure/reverse     — 역방향 추적 (Table → 화면)
  POST /api/v1/structure/graph/flow  — Full Stack Graph 탐색
  GET  /api/v1/structure/summary     — 프로젝트 구조 요약
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import verify_api_key
from app.api.models import NoriResponse
from app.db.connection import get_pool
from app.service.structure_search_service import StructureSearchService
from app.service.call_graph_service import CallGraphService
from app.service.feature_flow_builder import FeatureFlowBuilder

logger = logging.getLogger("nori-server")

router = APIRouter(
    prefix="/api/v1/structure",
    tags=["structure-search"],
    dependencies=[Depends(verify_api_key)],
)

# ── 서비스 싱글턴 (DB 풀 공유) ──
_struct_svc = StructureSearchService()
_graph_svc = CallGraphService()
_flow_builder = FeatureFlowBuilder()


def _check_db():
    if get_pool() is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="DB 풀이 초기화되지 않았습니다. 서버를 재시작하거나 DB 연결을 확인하세요.")


# ── 요청 모델 ──

class StructureSearchRequest(BaseModel):
    question: str = Field(..., description="자연어 질문")
    with_llm: bool = Field(False, description="LLM 요약 포함 여부")


class FlowTraceRequest(BaseModel):
    method_full_name: str = Field(..., description="메서드 full_name (예: MemberService.insert)")
    depth: int = Field(5, ge=1, le=10, description="탐색 깊이")


class DataFlowRequest(BaseModel):
    url: str = Field(..., description="API URL (예: /member/insert.do)")


class PageConnectionRequest(BaseModel):
    keyword: str = Field(..., description="페이지 키워드 (예: memberForm, 회원등록)")


class ImpactAnalysisRequest(BaseModel):
    method_full_name: str = Field(..., description="분석할 메서드 full_name")
    depth: int = Field(5, ge=1, le=10)


class ReverseTraceRequest(BaseModel):
    table_name: str = Field(..., description="DB 테이블명 (예: TB_MEMBER)")


class GraphFlowRequest(BaseModel):
    node_key: str = Field(..., description="시작 노드 key (파일경로, URL, 클래스명 등)")


class GraphSearchRequest(BaseModel):
    keyword: str = Field(..., description="검색 키워드")
    node_type: str | None = Field(None, description="노드 타입 필터 (PAGE/JS/API/CONTROLLER/SERVICE/MAPPER/SQL/TABLE)")


# ── 엔드포인트 ──

@router.post("/search", response_model=NoriResponse)
async def structure_search(req: StructureSearchRequest):
    """
    통합 구조 검색 — 질문의 intent를 자동 분석하여 적절한 구조 검색 수행.

    intent 분기:
    - struct_query      → 프로젝트 전체 구조 요약
    - feature_location  → 기능/클래스/메서드 위치
    - flow_trace        → 호출 흐름 추적
    - data_flow         → URL → Table 데이터 흐름
    - page_connection   → JSP → JS → API 연결
    - impact_analysis   → 영향도 분석
    - reverse_trace     → 테이블 기준 역추적
    """
    _check_db()
    result = await _flow_builder.build(req.question)

    if result.get("engine") == "embedding":
        return NoriResponse(
            data={
                "intent": result["intent"],
                "message": "이 질문은 임베딩 검색이 더 적합합니다. /api/v1/chat/efficient 를 사용하세요.",
            },
            search_depth="structure_fallback",
        )

    return NoriResponse(
        data={
            "intent": result["intent"],
            "intent_matched": result.get("intent_matched"),
            "structured_response": result.get("structured_response", ""),
            "raw": result.get("raw"),
        },
        search_depth="structure",
    )


@router.post("/flow", response_model=NoriResponse)
async def trace_flow(req: FlowTraceRequest):
    """호출 흐름 추적 — 메서드 full_name 기준 정방향 BFS"""
    _check_db()
    data = await _struct_svc.trace_flow(req.method_full_name, req.depth)
    from app.service.feature_flow_builder import _format_flow_trace
    return NoriResponse(
        data={"structured_response": _format_flow_trace(data), "raw": data},
        search_depth="structure",
    )


@router.post("/data-flow", response_model=NoriResponse)
async def trace_data_flow(req: DataFlowRequest):
    """데이터 흐름 추적 — URL 기준 Route → Controller → Mapper → SQL → Table"""
    _check_db()
    data = await _struct_svc.trace_data_flow(req.url)
    from app.service.feature_flow_builder import _format_data_flow
    return NoriResponse(
        data={"structured_response": _format_data_flow(data), "raw": data},
        search_depth="structure",
    )


@router.post("/page", response_model=NoriResponse)
async def trace_page_connection(req: PageConnectionRequest):
    """화면 연결 추적 — JSP → JS → API"""
    _check_db()
    data = await _struct_svc.trace_page_connection(req.keyword)
    from app.service.feature_flow_builder import _format_page_connection
    return NoriResponse(
        data={"structured_response": _format_page_connection(data), "raw": data},
        search_depth="structure",
    )


@router.post("/impact", response_model=NoriResponse)
async def analyze_impact(req: ImpactAnalysisRequest):
    """영향도 분석 — 메서드 변경 시 영향받는 상위 호출자 역방향 추적"""
    _check_db()
    data = await _struct_svc.analyze_impact(req.method_full_name, req.depth)
    from app.service.feature_flow_builder import _format_impact_analysis
    return NoriResponse(
        data={"structured_response": _format_impact_analysis(data), "raw": data},
        search_depth="structure",
    )


@router.post("/reverse", response_model=NoriResponse)
async def reverse_trace(req: ReverseTraceRequest):
    """역방향 추적 — 테이블 기준 Mapper → Controller → 화면 역추적"""
    _check_db()
    data = await _struct_svc.reverse_trace_table(req.table_name)
    from app.service.feature_flow_builder import _format_reverse_trace
    return NoriResponse(
        data={"structured_response": _format_reverse_trace(data), "raw": data},
        search_depth="structure",
    )


@router.post("/graph/flow", response_model=NoriResponse)
async def graph_full_stack(req: GraphFlowRequest):
    """Full Stack Graph 탐색 — 노드 key 기준 전체 스택 흐름"""
    _check_db()
    data = await _graph_svc.trace_full_stack(req.node_key)
    return NoriResponse(
        data=data,
        search_depth="structure",
    )


@router.post("/graph/search", response_model=NoriResponse)
async def graph_node_search(req: GraphSearchRequest):
    """그래프 노드 검색 — 타입 필터 + 키워드"""
    _check_db()
    nodes = await _graph_svc.find_graph_nodes(req.node_type, req.keyword)
    return NoriResponse(
        data={"nodes": nodes, "count": len(nodes)},
        search_depth="structure",
    )


@router.post("/feature", response_model=NoriResponse)
async def get_feature_flow(
    keyword: Annotated[str, Query(description="기능명 키워드")],
):
    """기능 흐름 조회 — 기능명으로 Full Stack Flow 조회"""
    _check_db()
    data = await _graph_svc.get_feature_flow(keyword)
    formatted = _graph_svc.build_search_response(keyword, data)
    return NoriResponse(
        data={"structured_response": formatted, "raw": data},
        search_depth="structure",
    )


@router.get("/summary", response_model=NoriResponse)
async def get_structure_summary():
    """프로젝트 구조 인덱스 현황 요약"""
    _check_db()
    data = await _struct_svc.get_project_structure_summary()
    from app.service.feature_flow_builder import _format_struct_summary
    return NoriResponse(
        data={"structured_response": _format_struct_summary(data), "raw": data},
        search_depth="structure",
    )
