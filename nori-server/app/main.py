"""
Nori AI — Java 개발 도우미 서버
FastAPI 메인 애플리케이션
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config.settings import get_settings
from app.api.deps import init_services
from app.api import system, code_writing, code_analysis, error_debug, search_chat, egov, project_context, memo, collector_admin, profile_api, chat_session, structure_search

# ── 로깅 설정 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nori-server")


# ── 라이프사이클 ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("=== Nori AI 서버 시작 ===")
    logger.info(f"AI Provider: {settings.ai_provider}")
    logger.info(f"AI Model: {settings.ai_model}")
    logger.info(f"ChromaDB: {settings.chroma_persist_dir}")

    await init_services(settings)
    logger.info("서비스 초기화 완료")

    yield

    from app.api.deps import close_services
    await close_services()
    logger.info("=== Nori AI 서버 종료 ===")


# ── FastAPI 앱 ──
app = FastAPI(
    title="Nori AI — Java 개발 도우미",
    description=(
        "Java/Spring/전자정부프레임워크 개발을 돕는 AI 도우미 서버.\n"
        "코드 자동완성, 에러 분석, 코드 생성, eGov 스캐폴딩 등 30개 API 제공."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ──
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 라우터 등록 ──
app.include_router(system.router)
app.include_router(code_writing.router)
app.include_router(code_analysis.router)
app.include_router(error_debug.router)
app.include_router(search_chat.router)
app.include_router(search_chat.ws_router)
app.include_router(egov.router)
app.include_router(project_context.router)
app.include_router(memo.router)
app.include_router(collector_admin.router)
app.include_router(profile_api.router)
app.include_router(chat_session.router)
app.include_router(structure_search.router)


# ── 전역 예외 핸들러 ──
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"처리되지 않은 에러: {exc}", exc_info=True)
    from fastapi.responses import JSONResponse
    err_msg = str(exc)[:200] if str(exc) else type(exc).__name__
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "data": None,
            "error": f"서버 내부 에러: {type(exc).__name__}",
            "detail": err_msg,  # 디버깅용 상세 메시지
        },
    )


@app.get("/")
async def root():
    """테스트 UI 페이지 제공"""
    static_dir = Path(__file__).resolve().parent.parent / "static"
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html")
    return {
        "name": "Nori AI — Java 개발 도우미",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/admin")
async def admin_page():
    """수집/임베딩 관리 대시보드"""
    static_dir = Path(__file__).resolve().parent.parent / "static"
    admin = static_dir / "admin.html"
    if admin.exists():
        return FileResponse(admin, media_type="text/html")
    return {"error": "admin.html not found"}
