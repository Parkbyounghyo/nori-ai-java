"""
프로젝트 컨텍스트 API — 프로젝트 분석, 의존성 분석, 라이브러리 추천, 프로젝트 스캔
"""
import logging

from fastapi import APIRouter, Depends

from app.api.deps import LlmDep, EmbeddingDep, verify_api_key
from app.api.models import (
    ProjectAnalyzeRequest, DependencyAnalyzeRequest,
    LibrarySuggestRequest, ProjectScanRequest, NoriResponse,
)

logger = logging.getLogger("nori-server")
router = APIRouter(
    prefix="/api/v1/context",
    tags=["project-context"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("/analyze", response_model=NoriResponse)
async def analyze_project(req: ProjectAnalyzeRequest, llm: LlmDep):
    """프로젝트 분석 — 구조, 의존성, 프레임워크 자동 감지"""
    context_parts = []
    if req.pom_xml:
        context_parts.append(f"pom.xml:\n```xml\n{req.pom_xml[:3000]}\n```")
    if req.build_gradle:
        context_parts.append(f"build.gradle:\n```groovy\n{req.build_gradle[:3000]}\n```")
    if req.file_list:
        context_parts.append(f"파일 목록 (일부):\n" + "\n".join(req.file_list[:50]))

    question = (
        "이 프로젝트를 분석해주세요.\n\n"
        "분석 항목:\n"
        "1. 사용 프레임워크/기술 (Spring Boot, eGov 등)\n"
        "2. Java 버전\n"
        "3. DB 종류\n"
        "4. 프로젝트 구조/아키텍처 패턴\n"
        "5. 주요 의존성\n"
        "6. 개선 제안\n\n"
        + "\n\n".join(context_parts)
    )

    analysis = await llm.chat(question=question)
    return NoriResponse(data={"analysis": analysis})


@router.post("/dependency", response_model=NoriResponse)
async def analyze_dependency(req: DependencyAnalyzeRequest, llm: LlmDep):
    """의존성 분석 — pom.xml/build.gradle 분석, 충돌/취약점"""
    build_file = ""
    if req.pom_xml:
        build_file = f"pom.xml:\n```xml\n{req.pom_xml[:5000]}\n```"
    elif req.build_gradle:
        build_file = f"build.gradle:\n```groovy\n{req.build_gradle[:5000]}\n```"

    question = (
        "이 빌드 파일의 의존성을 분석해주세요.\n\n"
        "분석 항목:\n"
        "1. 의존성 버전 호환성 체크\n"
        "2. 잠재적 버전 충돌\n"
        "3. 알려진 보안 취약점이 있는 버전\n"
        "4. 업데이트 권장 사항\n"
        "5. 불필요하거나 중복된 의존성\n\n"
        + build_file
    )

    analysis = await llm.chat(question=question)
    return NoriResponse(data={"dependency_analysis": analysis})


@router.post("/suggest", response_model=NoriResponse)
async def suggest_library(req: LibrarySuggestRequest, llm: LlmDep, emb: EmbeddingDep):
    """라이브러리 추천 — 기능 설명에 맞는 라이브러리 추천"""
    rag_context = ""
    if emb.is_available:
        results = await emb.search(
            query=f"java library {req.description}",
            top_k=3,
        )
        if results:
            rag_context = "\n---\n".join(r["text"][:500] for r in results)

    question = (
        f"이 기능을 구현하는 데 적합한 Java 라이브러리를 추천해주세요.\n\n"
        f"구현할 기능: {req.description}\n"
    )
    if req.project_type:
        question += f"프로젝트 타입: {req.project_type}\n"

    question += (
        "\n각 라이브러리에 대해:\n"
        "- 라이브러리 이름, groupId:artifactId\n"
        "- 장단점\n"
        "- Maven/Gradle 의존성 코드\n"
        "- 간단한 사용 예시\n"
    )

    answer = await llm.chat(question=question, rag_context=rag_context)
    return NoriResponse(data={"suggestions": answer})


@router.post("/scan", response_model=NoriResponse)
async def scan_project(req: ProjectScanRequest, llm: LlmDep):
    """프로젝트 전체 스캔 — 파일 구조 + 빌드 파일 분석하여 상세 레포트 생성"""
    context_parts = []

    if req.project_name:
        context_parts.append(f"프로젝트명: {req.project_name}")

    if req.pom_xml:
        context_parts.append(f"pom.xml:\n```xml\n{req.pom_xml[:5000]}\n```")
    if req.build_gradle:
        context_parts.append(f"build.gradle:\n```groovy\n{req.build_gradle[:5000]}\n```")
    if req.web_xml:
        context_parts.append(f"web.xml:\n```xml\n{req.web_xml[:3000]}\n```")
    if req.application_properties:
        context_parts.append(f"application.properties/yml:\n```\n{req.application_properties[:3000]}\n```")

    if req.file_tree:
        context_parts.append(f"프로젝트 파일 트리:\n{req.file_tree[:8000]}")

    question = (
        "이 프로젝트를 상세하게 분석하여 개발자가 항상 참고할 수 있는 프로젝트 레포트를 작성해주세요.\n\n"
        "다음 항목을 포함해서 정리해주세요:\n"
        "1. 프로젝트 개요 (한 줄 요약)\n"
        "2. 기술 스택 (프레임워크, Java 버전, 빌드 도구, DB)\n"
        "3. 프로젝트 구조 분석 (패키지/폴더 역할)\n"
        "4. 주요 의존성 라이브러리 목록\n"
        "5. 아키텍처 패턴 (MVC, 계층형, MSA 등)\n"
        "6. 설정 파일 분석 (DB 연결, 보안, 서버 설정)\n"
        "7. 코딩 컨벤션/스타일 추정\n"
        "8. 개선 제안 사항\n\n"
        + "\n\n".join(context_parts)
    )

    report = await llm.chat(question=question)
    return NoriResponse(data={"report": report})
