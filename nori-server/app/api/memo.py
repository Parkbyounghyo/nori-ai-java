"""
프로젝트 메모 API — DB 스키마 스캔, API 매핑 스캔, 에러 로그, 코딩 컨벤션
"""
import json
import logging
import re

from fastapi import APIRouter, Depends

from app.api.deps import LlmDep, verify_api_key, get_memo_service_dep
from app.api.models import NoriResponse
from app.service.memo_service import MemoService

from pydantic import BaseModel, Field
from typing import Annotated
from fastapi import Depends

logger = logging.getLogger("nori-server")

# MemoService DI
MemoDep = Annotated[MemoService, Depends(get_memo_service_dep)]


router = APIRouter(
    prefix="/api/v1/memo",
    tags=["project-memo"],
    dependencies=[Depends(verify_api_key)],
)


# ── DTO ──

class ScanSchemaRequest(BaseModel):
    source_files: list[str] = Field(..., description="VO 클래스, MyBatis XML, SQL 파일 내용들")
    project_type: str = Field("egov", description="프로젝트 타입 (egov, spring-boot)")


class ScanApiRequest(BaseModel):
    controller_files: list[str] = Field(..., description="Controller/Handler 소스 파일 내용들")
    project_type: str = Field("egov", description="프로젝트 타입")


class ErrorLogRequest(BaseModel):
    symptom: str = Field(..., description="에러 증상 (에러 메시지, 현상)")
    solution: str = Field(..., description="해결 방법")
    category: str = Field("", description="분류 (compile, runtime, config, db 등)")


class ConventionRequest(BaseModel):
    rules: list[str] = Field(..., description="코딩 컨벤션 규칙 리스트")


class MemoUpdateRequest(BaseModel):
    memo_type: str = Field(..., description="메모 타입 (db-schema, api-mapping, error-log, convention)")
    data: dict | list = Field(..., description="저장할 데이터")


# ── DB 스키마 스캔 ──

@router.post("/scan-schema", response_model=NoriResponse)
async def scan_schema(req: ScanSchemaRequest, llm: LlmDep, memo: MemoDep):
    """VO 클래스/MyBatis XML/SQL에서 DB 스키마(테이블/컬럼) 추출"""

    sources_text = "\n\n---\n\n".join(req.source_files[:30])  # 최대 30개 파일

    question = (
        "아래 Java/XML/SQL 소스에서 DB 테이블 스키마 정보를 추출해주세요.\n\n"
        "반드시 아래 JSON 형식으로만 응답하세요 (설명 없이 JSON만):\n"
        "```json\n"
        "[{\"table\": \"테이블명\", \"columns\": ["
        "{\"name\": \"컬럼명\", \"type\": \"자바타입\", \"pk\": true/false, "
        "\"fk\": \"참조테이블.참조컬럼 또는 null\", \"comment\": \"설명\"}"
        "]}]\n```\n\n"
        "추출 기준:\n"
        "- VO/DTO 클래스의 필드명 → 컬럼명 (camelCase→SNAKE_CASE 변환)\n"
        "- MyBatis XML의 resultMap, <insert>, <select> SQL에서 테이블/컬럼 추출\n"
        "- @Id, @Column 어노테이션에서 PK/컬럼 정보 추출\n"
        "- @Table 어노테이션에서 테이블명 추출\n\n"
        f"소스 코드:\n{sources_text[:15000]}"
    )

    result = await llm.chat(question=question)

    # LLM 응답에서 JSON 추출
    tables = _extract_json_from_response(result)
    if tables and isinstance(tables, list):
        memo.save_schema(tables)
        return NoriResponse(data={
            "tables": tables,
            "count": len(tables),
            "message": f"{len(tables)}개 테이블 스키마 추출 완료",
        })

    return NoriResponse(data={
        "raw_response": result,
        "message": "스키마 추출 완료 (수동 검토 필요)",
    })


# ── API 매핑 스캔 ──

@router.post("/scan-api", response_model=NoriResponse)
async def scan_api(req: ScanApiRequest, llm: LlmDep, memo: MemoDep):
    """Controller 파일에서 API 엔드포인트 매핑 추출"""

    sources_text = "\n\n---\n\n".join(req.controller_files[:20])

    question = (
        "아래 Java Controller 소스에서 모든 API 엔드포인트를 추출해주세요.\n\n"
        "반드시 아래 JSON 형식으로만 응답하세요 (설명 없이 JSON만):\n"
        "```json\n"
        "[{\"method\": \"GET/POST/PUT/DELETE\", \"uri\": \"/api/path\", "
        "\"handler\": \"ControllerName.methodName\", \"description\": \"기능 설명\"}]\n"
        "```\n\n"
        "추출 기준:\n"
        "- @RequestMapping, @GetMapping, @PostMapping 등의 URI\n"
        "- 클래스 레벨 @RequestMapping + 메서드 레벨 합산\n"
        "- eGov의 경우 @Controller + @RequestMapping 패턴\n\n"
        f"소스 코드:\n{sources_text[:15000]}"
    )

    result = await llm.chat(question=question)

    endpoints = _extract_json_from_response(result)
    if endpoints and isinstance(endpoints, list):
        memo.save_api_mapping(endpoints)
        return NoriResponse(data={
            "endpoints": endpoints,
            "count": len(endpoints),
            "message": f"{len(endpoints)}개 API 엔드포인트 추출 완료",
        })

    return NoriResponse(data={
        "raw_response": result,
        "message": "API 매핑 추출 완료 (수동 검토 필요)",
    })


# ── DB 스키마 조회 ──

@router.get("/schema", response_model=NoriResponse)
async def get_schema(memo: MemoDep):
    """저장된 DB 스키마 조회"""
    data = memo.get_schema()
    if data is None:
        return NoriResponse(data={"tables": [], "message": "저장된 스키마 없음. /scan-schema로 먼저 스캔하세요."})
    return NoriResponse(data=data)


# ── API 매핑 조회 ──

@router.get("/api-mapping", response_model=NoriResponse)
async def get_api_mapping(memo: MemoDep):
    """저장된 API 매핑 조회"""
    data = memo.get_api_mapping()
    if data is None:
        return NoriResponse(data={"endpoints": [], "message": "저장된 API 매핑 없음. /scan-api로 먼저 스캔하세요."})
    return NoriResponse(data=data)


# ── 에러 로그 ──

@router.post("/error-log", response_model=NoriResponse)
async def add_error_log(req: ErrorLogRequest, memo: MemoDep):
    """에러 해결 기록 추가"""
    memo.add_error_log(req.symptom, req.solution, req.category)
    logs = memo.get_error_logs()
    return NoriResponse(data={
        "total": len(logs),
        "message": "에러 기록 추가 완료",
    })


@router.get("/error-log", response_model=NoriResponse)
async def get_error_logs(memo: MemoDep):
    """에러 해결 기록 전체 조회"""
    logs = memo.get_error_logs()
    return NoriResponse(data={"logs": logs, "total": len(logs)})


# ── 코딩 컨벤션 ──

@router.post("/convention", response_model=NoriResponse)
async def save_convention(req: ConventionRequest, memo: MemoDep):
    """코딩 컨벤션 규칙 저장"""
    memo.save_convention(req.rules)
    return NoriResponse(data={
        "rules_count": len(req.rules),
        "message": "코딩 컨벤션 저장 완료",
    })


@router.get("/convention", response_model=NoriResponse)
async def get_convention(memo: MemoDep):
    """코딩 컨벤션 조회"""
    data = memo.get_convention()
    if data is None:
        return NoriResponse(data={"rules": [], "message": "저장된 컨벤션 없음"})
    return NoriResponse(data=data)


# ── 전체 메모 컨텍스트 ──

@router.get("/context", response_model=NoriResponse)
async def get_memo_context(memo: MemoDep):
    """LLM에 주입되는 전체 메모 컨텍스트 조회"""
    ctx = memo.get_context()
    return NoriResponse(data={"context": ctx, "length": len(ctx)})


# ── 유틸 ──

def _extract_json_from_response(text: str) -> list | dict | None:
    """LLM 응답에서 JSON 부분만 추출"""
    # ```json ... ``` 블록 추출
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # [ ... ] 또는 { ... } 직접 추출
    for pattern in [r'\[.*\]', r'\{.*\}']:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue

    return None
