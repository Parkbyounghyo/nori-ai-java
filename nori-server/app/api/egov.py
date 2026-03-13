"""
전자정부프레임워크(eGovFrame) 전용 API
스캐폴딩, 코드 변환, 설정 가이드, 공통컴포넌트, SQL 매퍼, 마이그레이션, 패턴 검사
"""
import logging

from fastapi import APIRouter, Depends

from app.api.deps import LlmDep, EmbeddingDep, verify_api_key
from app.api.models import NoriResponse

from pydantic import BaseModel, Field

logger = logging.getLogger("nori-server")
router = APIRouter(
    prefix="/api/v1/egov",
    tags=["egov"],
    dependencies=[Depends(verify_api_key)],
)


# ── eGov 전용 DTO ──
class ScaffoldRequest(BaseModel):
    entity_name: str = Field(..., description="엔티티 이름 (예: Board, User)")
    fields: list[dict] = Field(default_factory=list, description="필드 목록 [{name, type, comment}]")
    use_mybatis: bool = Field(True, description="MyBatis 사용 여부 (False면 JPA)")
    package_base: str = Field("egovframework.example", description="기본 패키지")
    include_common: bool = Field(True, description="공통VO(DefaultVO) 상속 여부")


class ConvertRequest(BaseModel):
    code: str = Field(..., description="변환할 코드")
    direction: str = Field("springboot-to-egov", description="변환 방향")
    # springboot-to-egov | egov-to-springboot


class EgovConfigRequest(BaseModel):
    config_type: str = Field("datasource", description="설정 유형")
    # datasource | sqlmap | security | aspect | transaction | common
    db_type: str = Field("oracle", description="DB 종류")
    details: str = Field("", description="추가 요구사항")


class CommonComponentRequest(BaseModel):
    component_name: str = Field(..., description="공통컴포넌트 이름")
    # login | board | authority | code | file | message | schedule | batch
    detail: str = Field("", description="추가 질문")


class SqlMapperRequest(BaseModel):
    vo_code: str = Field("", description="VO 클래스 코드")
    table_name: str = Field("", description="테이블 이름")
    db_type: str = Field("oracle", description="DB 종류 (oracle, mysql, postgresql)")
    operations: list[str] = Field(
        default_factory=lambda: ["select", "selectList", "insert", "update", "delete"],
        description="생성할 SQL 종류",
    )


class MigrationRequest(BaseModel):
    from_version: str = Field("3.x", description="현재 eGov 버전")
    to_version: str = Field("4.x", description="대상 eGov 버전")
    code: str = Field("", description="마이그레이션 대상 코드")
    concern: str = Field("", description="특별히 궁금한 점")


class PatternCheckRequest(BaseModel):
    code: str = Field(..., description="검사할 코드")
    layer: str = Field("", description="레이어 (controller, service, dao)")


# ── API 엔드포인트 ──

@router.post("/scaffold", response_model=NoriResponse)
async def scaffold(req: ScaffoldRequest, llm: LlmDep, emb: EmbeddingDep):
    """eGov 스캐폴딩 — 표준 레이어 구조 코드 생성 (Controller→Service→DAO→VO)"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query="eGovFrame scaffold controller service dao vo pattern",
            top_k=5,
            collections=["egov"],
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    fields_desc = "\n".join(
        f"  - {f.get('name', '')}: {f.get('type', 'String')} // {f.get('comment', '')}"
        for f in req.fields
    ) if req.fields else "(필드 정보 없음 — 기본 CRUD 패턴으로 생성)"

    mapper_type = "MyBatis" if req.use_mybatis else "JPA"
    common_vo = "DefaultVO를 상속" if req.include_common else "독립 VO"

    description = (
        f"전자정부프레임워크(eGovFrame) 표준 패턴으로 '{req.entity_name}' "
        f"CRUD 전체 레이어를 생성해주세요.\n\n"
        f"패키지: {req.package_base}\n"
        f"ORM: {mapper_type}\n"
        f"VO 방식: {common_vo}\n"
        f"필드:\n{fields_desc}\n\n"
        f"생성할 파일:\n"
        f"1. {req.entity_name}Controller.java\n"
        f"2. {req.entity_name}Service.java (인터페이스)\n"
        f"3. {req.entity_name}ServiceImpl.java (EgovAbstractServiceImpl 상속)\n"
        f"4. {req.entity_name}{'Mapper' if req.use_mybatis else 'Repository'}.java\n"
        f"5. {req.entity_name}VO.java\n"
        f"6. {req.entity_name}_SQL.xml (MyBatis SQL 매퍼)\n" if req.use_mybatis else ""
    )

    code = await llm.generate_code(
        description=description,
        language="java",
        project_context=f"전자정부프레임워크 / {mapper_type}",
        rag_context=rag_context,
    )
    return NoriResponse(data={
        "scaffold_code": code,
        "entity": req.entity_name,
        "pattern": mapper_type,
    })


@router.post("/convert", response_model=NoriResponse)
async def convert(req: ConvertRequest, llm: LlmDep, emb: EmbeddingDep):
    """Spring Boot ↔ eGov 코드 패턴 변환"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query="eGov Spring Boot conversion pattern difference",
            top_k=5,
            collections=["egov", "spring", "community"],
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    if req.direction == "springboot-to-egov":
        desc = (
            "이 Spring Boot 코드를 전자정부프레임워크(eGovFrame) 패턴으로 변환해주세요.\n"
            "변환 규칙:\n"
            "- @RestController → @Controller + @RequestMapping\n"
            "- 생성자 주입 → @Resource 주입\n"
            "- JPA Repository → MyBatis DAO\n"
            "- application.yml → context-*.xml\n"
            "- ResponseEntity → ModelAndView\n"
            "- Lombok 제거 → getter/setter 직접 작성\n\n"
        )
    else:
        desc = (
            "이 전자정부프레임워크(eGovFrame) 코드를 Spring Boot 패턴으로 변환해주세요.\n"
            "변환 규칙:\n"
            "- @Controller → @RestController\n"
            "- @Resource → 생성자 주입\n"
            "- MyBatis DAO → JPA Repository\n"
            "- context-*.xml → application.yml + @Configuration\n"
            "- ModelAndView → ResponseEntity\n"
            "- DefaultVO → Pageable\n\n"
        )

    desc += f"코드:\n```java\n{req.code}\n```"

    result = await llm.generate_code(
        description=desc,
        language="java",
        rag_context=rag_context,
    )
    return NoriResponse(data={
        "converted_code": result,
        "direction": req.direction,
    })


@router.post("/config", response_model=NoriResponse)
async def egov_config(req: EgovConfigRequest, llm: LlmDep, emb: EmbeddingDep):
    """eGov 설정 가이드 — context-*.xml 설정 도우미"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query=f"eGov {req.config_type} configuration {req.db_type}",
            top_k=5,
            collections=["egov"],
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:600] for r in results)

    desc = (
        f"전자정부프레임워크의 {req.config_type} 설정 파일을 생성해주세요.\n"
        f"DB: {req.db_type}\n"
    )
    if req.details:
        desc += f"추가 요구사항: {req.details}\n"

    result = await llm.generate_code(
        description=desc,
        language="xml",
        project_context="전자정부프레임워크 설정",
        rag_context=rag_context,
    )
    return NoriResponse(data={
        "config": result,
        "config_type": req.config_type,
    })


@router.post("/common", response_model=NoriResponse)
async def common_component(req: CommonComponentRequest, llm: LlmDep, emb: EmbeddingDep):
    """공통컴포넌트 사용법 안내"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query=f"eGov 공통컴포넌트 {req.component_name} 사용법",
            top_k=5,
            collections=["egov", "community"],
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:600] for r in results)

    question = f"전자정부프레임워크 '{req.component_name}' 공통컴포넌트 사용법을 알려주세요."
    if req.detail:
        question += f"\n특히: {req.detail}"

    answer = await llm.chat(
        question=question,
        rag_context=rag_context,
    )
    return NoriResponse(data={
        "guide": answer,
        "component": req.component_name,
    })


@router.post("/sql", response_model=NoriResponse)
async def sql_mapper(req: SqlMapperRequest, llm: LlmDep, emb: EmbeddingDep):
    """SQL 매퍼 생성 — VO 기반 MyBatis SQL 매퍼 자동 생성"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query=f"MyBatis SQL mapper {req.db_type} eGov",
            top_k=5,
            collections=["egov", "community"],
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    desc = (
        f"MyBatis SQL 매퍼를 생성해주세요.\n"
        f"DB: {req.db_type}\n"
        f"테이블: {req.table_name}\n"
        f"생성할 SQL: {', '.join(req.operations)}\n"
    )
    if req.vo_code:
        desc += f"\nVO 클래스:\n```java\n{req.vo_code}\n```\n"

    desc += (
        f"\n규칙:\n"
        f"- 파라미터 바인딩은 #{{}} 사용 (SQL Injection 방지)\n"
        f"- eGov 표준 네이밍: select[Entity], selectList[Entity], insert[Entity]\n"
        f"- 페이징 처리 포함 (ROWNUM for Oracle, LIMIT for MySQL)\n"
    )

    result = await llm.generate_code(
        description=desc,
        language="xml",
        project_context="전자정부프레임워크 MyBatis",
        rag_context=rag_context,
    )
    return NoriResponse(data={
        "sql_mapper": result,
        "table": req.table_name,
        "db_type": req.db_type,
    })


@router.post("/migration", response_model=NoriResponse)
async def migration(req: MigrationRequest, llm: LlmDep, emb: EmbeddingDep):
    """버전 마이그레이션 가이드 — 구버전→신버전 변경점/코드 변환"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query=f"eGov {req.from_version} to {req.to_version} migration",
            top_k=5,
            collections=["egov", "community"],
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:600] for r in results)

    question = (
        f"전자정부프레임워크 {req.from_version}에서 {req.to_version}으로 "
        f"마이그레이션하는 방법을 안내해주세요.\n"
        f"변경사항, 호환성 문제, 단계별 가이드를 포함해주세요.\n"
    )
    if req.code:
        question += f"\n이 코드를 변환해주세요:\n```java\n{req.code}\n```\n"
    if req.concern:
        question += f"\n특히 궁금한 점: {req.concern}\n"

    answer = await llm.chat(question=question, rag_context=rag_context)
    return NoriResponse(data={
        "migration_guide": answer,
        "from_version": req.from_version,
        "to_version": req.to_version,
    })


@router.post("/pattern", response_model=NoriResponse)
async def pattern_check(req: PatternCheckRequest, llm: LlmDep, emb: EmbeddingDep):
    """코딩 패턴 검사 — eGov 표준 패턴 준수 여부 검증"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query="eGov coding pattern standard convention layer",
            top_k=5,
            collections=["egov"],
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    layer_hint = f"(레이어: {req.layer})" if req.layer else ""

    question = (
        f"이 코드가 전자정부프레임워크(eGovFrame) 표준 코딩 패턴을 "
        f"잘 지키고 있는지 검사해주세요.{layer_hint}\n\n"
        f"검사 항목:\n"
        f"1. 레이어 구조 준수 (Controller→Service→DAO→VO)\n"
        f"2. 네이밍 컨벤션\n"
        f"3. 의존성 주입 패턴 (@Resource)\n"
        f"4. 에러 처리 패턴 (EgovBizException)\n"
        f"5. 트랜잭션 처리\n"
        f"6. 보안 (SQL Injection, XSS 등)\n\n"
        f"코드:\n```java\n{req.code}\n```"
    )

    review = await llm.chat(question=question, rag_context=rag_context)
    return NoriResponse(data={
        "pattern_review": review,
        "layer": req.layer,
    })
