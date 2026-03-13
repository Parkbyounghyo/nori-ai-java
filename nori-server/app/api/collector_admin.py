"""
수집/임베딩 관리 API — 상태 조회, 임베딩 실행, URL 수집, 새로고침, 시스템 모니터
"""
import asyncio
import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import psutil
import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import EmbeddingDep, verify_api_key
from app.api.models import NoriResponse
from app.config.settings import get_settings

logger = logging.getLogger("nori-server")

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["collector-admin"],
    dependencies=[Depends(verify_api_key)],
)

# ── 경로 계산 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # nori-ai-java
_COLLECTOR_DATA = _PROJECT_ROOT / "nori-collector" / "data"
_COLLECTOR_MAIN = _PROJECT_ROOT / "nori-collector" / "main.py"
_EMBEDDING_MAIN = _PROJECT_ROOT / "nori-embedding" / "main.py"
_COLLECTOR_CONFIG = _PROJECT_ROOT / "nori-collector" / "config.yaml"

# 타겟별 데이터 디렉토리 매핑 (config.yaml의 output_dir 기준)
_TARGET_DIR_MAP = {
    "javadoc":    "javadoc",
    "spring":     "spring-docs",
    "web-ui":     "web-ui",
    "desktop-ui": "desktop-ui",
    "community":  "community",
    "egov":       "egov",
    "database":   "database",
    "custom":     "custom",
}

# 임베딩 실행 상태 추적
_embed_status = {
    "running": False,
    "target": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "success": None,
}

# 수집 실행 상태 추적 (타겟별)
_collect_status: dict[str, dict] = {}
# 예: {"spring": {"running": True, "message": "수집 중...", "started_at": "..."}}

# 최근 완료 작업 기록 (최대 20건)
_recent_tasks: list[dict] = []

# 임베딩 벡터 통계 캐시 (임베딩 실행 중 DB 락 회피용)
_cached_embed_stats: dict[str, int] = {}


# ── DTO ──
class EmbedRequest(BaseModel):
    targets: list[str] = Field(..., description="임베딩할 타겟 목록 (javadoc, spring, ...)")


class CollectUrlRequest(BaseModel):
    url: str = Field(..., description="수집할 URL")
    category: str = Field("custom", description="카테고리 (custom, web-ui, community 등)")
    max_depth: int = Field(1, ge=0, le=3, description="최대 탐색 깊이")
    max_pages: int = Field(20, ge=1, le=100, description="최대 페이지 수")


class RefreshRequest(BaseModel):
    target: str = Field(..., description="새로고침할 타겟 (javadoc, spring, ...)")


class RefreshSourcesRequest(BaseModel):
    target: str = Field(..., description="타겟 (spring, web-ui 등)")
    source_names: list[str] = Field(..., description="갱신할 소스 이름 목록")


# ── 상태 조회 ──
@router.get("/status", response_model=NoriResponse)
async def collector_status(emb: EmbeddingDep):
    """수집 파일 수 vs 임베딩 벡터 수 비교 대시보드 데이터"""
    settings = get_settings()

    # 1) 수집된 파일 수 카운트
    collected = {}
    for target, sub_dir in _TARGET_DIR_MAP.items():
        data_path = _COLLECTOR_DATA / sub_dir
        if data_path.is_dir():
            json_files = list(data_path.rglob("*.json"))
            # _progress.json 제외
            count = sum(1 for f in json_files if f.name != "_progress.json")
            collected[target] = count
        else:
            collected[target] = 0

    # 2) 임베딩 벡터 수 조회 (임베딩 실행 중이면 DB 락 회피를 위해 스킵)
    if _embed_status["running"]:
        embedded = _cached_embed_stats
    else:
        emb_stats = await emb.get_stats()
        embedded = emb_stats.get("collections", {})
        _cached_embed_stats.update(embedded)

    # 3) 결합
    status_list = []
    for target, sub_dir in _TARGET_DIR_MAP.items():
        col_count = collected.get(target, 0)
        # 임베딩 컬렉션 이름은 SOURCE_TO_COLLECTION 기준 (spring-doc → spring 등)
        embed_col_name = target
        if target == "spring":
            embed_col_name = "spring"
        emb_count = embedded.get(embed_col_name, 0)
        status_list.append({
            "target": target,
            "collected_files": col_count,
            "embedded_chunks": emb_count,
            "status": "synced" if (col_count > 0 and emb_count > 0)
                      else ("empty" if col_count == 0 else "not_embedded"),
            "data_dir": str(_COLLECTOR_DATA / sub_dir),
        })

    return NoriResponse(data={
        "sources": status_list,
        "embed_running": _embed_status["running"],
        "embed_target": _embed_status["target"],
        "embed_message": _embed_status["message"],
        "embed_started_at": _embed_status["started_at"],
        "embed_finished_at": _embed_status["finished_at"],
        "embed_success": _embed_status["success"],
        "collect_status": {
            k: v for k, v in _collect_status.items()
        },
    })


# ── 임베딩 실행 ──
@router.post("/embed", response_model=NoriResponse)
async def trigger_embedding(req: EmbedRequest):
    """미임베딩 데이터를 벡터 DB에 적재 (백그라운드 실행)"""
    if _embed_status["running"]:
        return NoriResponse(
            success=False,
            error=f"이미 임베딩 실행 중: {_embed_status['target']}",
        )

    valid_targets = list(_TARGET_DIR_MAP.keys())
    invalid = [t for t in req.targets if t not in valid_targets]
    if invalid:
        return NoriResponse(
            success=False,
            error=f"잘못된 타겟: {invalid}. 사용 가능: {valid_targets}",
        )

    # 백그라운드로 임베딩 파이프라인 실행
    _embed_status["running"] = True
    _embed_status["target"] = ", ".join(req.targets)
    _embed_status["message"] = "임베딩 시작 중..."
    _embed_status["started_at"] = datetime.now().strftime("%H:%M:%S")
    _embed_status["finished_at"] = None
    _embed_status["success"] = None

    asyncio.create_task(_run_embedding_pipeline(req.targets))

    return NoriResponse(data={
        "started": True,
        "targets": req.targets,
        "message": f"{len(req.targets)}개 타겟 임베딩을 백그라운드에서 시작합니다.",
    })


def _run_subprocess(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    """스레드에서 실행할 subprocess.run 래퍼 (Windows 호환)"""
    return subprocess.run(
        cmd, capture_output=True, cwd=cwd,
    )


async def _run_embedding_pipeline(targets: list[str]):
    """subprocess로 임베딩 파이프라인 실행 (Windows 호환: to_thread 사용)"""
    try:
        cmd = [sys.executable, str(_EMBEDDING_MAIN), "--target"] + targets
        logger.info(f"임베딩 파이프라인 실행: {' '.join(cmd)}")

        result = await asyncio.to_thread(
            _run_subprocess, cmd, str(_PROJECT_ROOT / "nori-embedding"),
        )

        finished_at = datetime.now().strftime("%H:%M:%S")
        if result.returncode == 0:
            _embed_status["message"] = f"완료! ({', '.join(targets)})"
            _embed_status["success"] = True
            _add_recent_task(f"임베딩: {', '.join(targets)}", "embed", True, "완료")
            logger.info(f"임베딩 완료: {result.stdout.decode('utf-8', errors='replace')[-500:]}")
        else:
            _embed_status["message"] = f"실패 (코드: {result.returncode})"
            _embed_status["success"] = False
            _add_recent_task(f"임베딩: {', '.join(targets)}", "embed", False, f"코드: {result.returncode}")
            logger.error(f"임베딩 실패: {result.stderr.decode('utf-8', errors='replace')[-500:]}")
        _embed_status["finished_at"] = finished_at

    except Exception as e:
        _embed_status["message"] = f"에러: {e}"
        _embed_status["success"] = False
        _embed_status["finished_at"] = datetime.now().strftime("%H:%M:%S")
        _add_recent_task(f"임베딩: {', '.join(targets)}", "embed", False, str(e))
        logger.error(f"임베딩 파이프라인 에러: {e}", exc_info=True)
    finally:
        _embed_status["running"] = False


# ── URL 수집 ──
@router.post("/collect-url", response_model=NoriResponse)
async def collect_url(req: CollectUrlRequest):
    """URL에서 문서를 수집하여 데이터 디렉토리에 저장"""
    import hashlib
    import aiohttp

    target_dir = _COLLECTOR_DATA / _TARGET_DIR_MAP.get(req.category, "custom")
    target_dir.mkdir(parents=True, exist_ok=True)

    # 중복 체크: _progress.json에서 이미 수집된 URL인지 확인
    progress_file = target_dir / "_progress.json"
    collected_urls = set()
    if progress_file.exists():
        with open(progress_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            collected_urls = set(data.get("collected_urls", []))

    if req.url in collected_urls:
        return NoriResponse(data={
            "added": 0,
            "message": f"이미 수집된 URL입니다: {req.url}",
            "duplicate": True,
        })

    # URL에서 콘텐츠 수집
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            collected_pages = []
            await _crawl_page(session, req.url, req.max_depth, req.max_pages,
                              collected_pages, set(), collected_urls)

            if not collected_pages:
                return NoriResponse(
                    success=False,
                    error="URL에서 콘텐츠를 가져올 수 없습니다.",
                )

            # 파일 저장
            saved = 0
            for page in collected_pages:
                url_hash = hashlib.sha256(page["url"].encode()).hexdigest()[:16]
                filename = f"{req.category}_{url_hash}.json"
                filepath = target_dir / filename

                doc = {
                    "url": page["url"],
                    "title": page.get("title", ""),
                    "content": page["content"],
                    "source_type": req.category,
                    "category": req.category,
                }

                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(doc, ensure_ascii=False, indent=2, fp=f)

                collected_urls.add(page["url"])
                saved += 1

            # progress 업데이트
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump({"collected_urls": list(collected_urls)}, f)

            return NoriResponse(data={
                "added": saved,
                "message": f"{saved}개 페이지 수집 완료 (카테고리: {req.category})",
                "duplicate": False,
            })

    except Exception as e:
        logger.error(f"URL 수집 실패: {e}", exc_info=True)
        return NoriResponse(success=False, error=f"수집 실패: {e}")


async def _crawl_page(session, url: str, depth: int, max_pages: int,
                      results: list, visited: set, existing_urls: set):
    """재귀적 페이지 크롤링"""
    if len(results) >= max_pages or url in visited or url in existing_urls:
        return
    visited.add(url)

    try:
        async with session.get(url, headers={
            "User-Agent": "NoriAI-Collector/1.0 (Java-Dev-Helper)"
        }) as resp:
            if resp.status != 200:
                return
            ct = resp.content_type or ""
            if "html" not in ct and "text" not in ct:
                return
            html = await resp.text(errors="replace")
    except Exception:
        return

    # 제목 추출
    title = ""
    import re
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = title_match.group(1).strip()

    # HTML → 텍스트 (간단 변환)
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > 100:  # 최소 길이 필터
        results.append({"url": url, "title": title, "content": text[:50000]})

    # depth > 0이면 링크 따라가기
    if depth > 0 and len(results) < max_pages:
        from urllib.parse import urljoin, urlparse
        base_domain = urlparse(url).netloc
        links = re.findall(r'href=["\']([^"\']+)["\']', html)
        for link in links[:50]:  # 링크 수 제한
            full_url = urljoin(url, link)
            parsed = urlparse(full_url)
            # 같은 도메인만, fragment 제거
            if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if parsed.query:
                    clean_url += f"?{parsed.query}"
                await _crawl_page(session, clean_url, depth - 1, max_pages,
                                  results, visited, existing_urls)


# ── 업데이트 수집 (기존 유지 + 새 것만 추가) ──
@router.post("/update", response_model=NoriResponse)
async def update_target(req: RefreshRequest):
    """기존 데이터는 유지하고 새로운 문서만 추가 수집"""
    valid_targets = list(_TARGET_DIR_MAP.keys())
    if req.target not in valid_targets:
        return NoriResponse(
            success=False,
            error=f"잘못된 타겟: {req.target}. 사용 가능: {valid_targets}",
        )

    if req.target == "custom":
        return NoriResponse(
            success=False,
            error="custom 타겟은 업데이트할 수 없습니다.",
        )

    # 이미 수집 중인지 확인
    if req.target in _collect_status and _collect_status[req.target].get("running"):
        return NoriResponse(
            success=False,
            error=f"이미 '{req.target}' 수집이 실행 중입니다.",
        )

    # progress를 유지한 채 수집기 실행 → 이미 수집된 URL은 자동으로 건너뜀
    asyncio.create_task(_run_collector(req.target))

    return NoriResponse(data={
        "started": True,
        "target": req.target,
        "message": f"'{req.target}' 업데이트 수집 시작 — 기존 데이터는 유지하고 새 문서만 추가합니다.",
    })


# ── 새로 수집 (초기화 후 전체 수집) ──
@router.post("/refresh", response_model=NoriResponse)
async def refresh_target(req: RefreshRequest):
    """수집 이력을 초기화하고 처음부터 새로 수집"""
    valid_targets = list(_TARGET_DIR_MAP.keys())
    if req.target not in valid_targets:
        return NoriResponse(
            success=False,
            error=f"잘못된 타겟: {req.target}. 사용 가능: {valid_targets}",
        )

    if req.target == "custom":
        return NoriResponse(
            success=False,
            error="custom 타겟은 새로고침할 수 없습니다.",
        )

    # _progress.json 초기화 (기존 파일은 유지, 수집 이력만 리셋)
    target_dir = _COLLECTOR_DATA / _TARGET_DIR_MAP[req.target]
    progress_file = target_dir / "_progress.json"
    if progress_file.exists():
        # 기존 진행 상태를 백업 후 리셋
        backup = progress_file.with_suffix(".json.bak")
        progress_file.rename(backup)

    # 이미 수집 중인지 확인
    if req.target in _collect_status and _collect_status[req.target].get("running"):
        return NoriResponse(
            success=False,
            error=f"이미 '{req.target}' 수집이 실행 중입니다.",
        )

    # 백그라운드로 수집 실행
    asyncio.create_task(_run_collector(req.target))

    return NoriResponse(data={
        "started": True,
        "target": req.target,
        "message": f"'{req.target}' 수집을 백그라운드에서 시작합니다. 완료 후 임베딩도 실행해 주세요.",
    })


async def _run_collector(target: str):
    """subprocess로 수집기 실행 (상태 추적 포함)"""
    _collect_status[target] = {
        "running": True,
        "message": "수집 시작 중...",
        "started_at": datetime.now().strftime("%H:%M:%S"),
        "finished_at": None,
        "success": None,
    }
    try:
        cmd = [sys.executable, str(_COLLECTOR_MAIN), "--target", target]
        logger.info(f"수집기 실행: {' '.join(cmd)}")

        _collect_status[target]["message"] = "수집 진행 중..."

        result = await asyncio.to_thread(
            _run_subprocess, cmd, str(_PROJECT_ROOT / "nori-collector"),
        )

        finished_at = datetime.now().strftime("%H:%M:%S")

        if result.returncode == 0:
            _collect_status[target]["message"] = f"수집 완료! ({finished_at})"
            _collect_status[target]["success"] = True
            _add_recent_task(f"수집: {target}", "collect", True, "완료")
            logger.info(f"수집 완료 ({target}): {result.stdout.decode('utf-8', errors='replace')[-500:]}")
        else:
            _collect_status[target]["message"] = f"수집 실패 (코드: {result.returncode})"
            _collect_status[target]["success"] = False
            _add_recent_task(f"수집: {target}", "collect", False, f"코드: {result.returncode}")
            logger.error(f"수집 실패 ({target}): {result.stderr.decode('utf-8', errors='replace')[-500:]}")

        _collect_status[target]["finished_at"] = finished_at

    except Exception as e:
        _collect_status[target]["message"] = f"에러: {e}"
        _collect_status[target]["success"] = False
        _add_recent_task(f"수집: {target}", "collect", False, str(e))
        logger.error(f"수집기 실행 에러 ({target}): {e}", exc_info=True)
    finally:
        _collect_status[target]["running"] = False


# ── 임베딩 진행 상태 ──
@router.get("/embed-status", response_model=NoriResponse)
async def embed_status():
    """현재 임베딩 실행 상태 확인"""
    return NoriResponse(data=_embed_status)


# ── 수집 진행 상태 ──
@router.get("/collect-status", response_model=NoriResponse)
async def collect_status():
    """현재 수집 실행 상태 확인 (전체 타겟)"""
    return NoriResponse(data={
        "targets": _collect_status,
        "any_running": any(v.get("running") for v in _collect_status.values()),
    })


# ── 수집대상 목록 조회 ──
# config.yaml 키 → API 타겟 매핑
_CONFIG_KEY_MAP = {
    "javadoc":    "javadoc",
    "spring":     "spring",
    "web-ui":     "web_ui",
    "desktop-ui": "desktop_ui",
    "community":  "community",
    "egov":       "egov",
    "database":   "database",
}


def _load_config() -> dict:
    """config.yaml 로드 (캐시 없이 최신)"""
    if _COLLECTOR_CONFIG.exists():
        with open(_COLLECTOR_CONFIG, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


@router.get("/sources/{target}", response_model=NoriResponse)
async def get_sources(target: str):
    """특정 타겟의 수집대상 소스 목록 반환"""
    config_key = _CONFIG_KEY_MAP.get(target)
    if not config_key:
        return NoriResponse(
            success=False,
            error=f"알 수 없는 타겟: {target}",
        )

    config = _load_config()
    targets = config.get("targets", {})
    target_config = targets.get(config_key, {})

    sources = []

    # javadoc은 특수 구조 (packages 기반)
    if target == "javadoc":
        base_url = target_config.get("base_url", "")
        packages = target_config.get("packages", [])
        for pkg in packages:
            sources.append({
                "name": pkg,
                "url": f"{base_url}/{pkg.replace('.', '/')}/package-summary.html",
                "category": "javadoc",
            })
    else:
        # sources 기반 타겟
        for src in target_config.get("sources", []):
            entry = {"name": src.get("name", "unknown")}
            if src.get("url"):
                entry["url"] = src["url"]
            if src.get("start_urls"):
                entry["url"] = src["start_urls"][0]
                if len(src["start_urls"]) > 1:
                    entry["url"] += f" 외 {len(src['start_urls']) - 1}개"
            if src.get("tags"):
                entry["tags"] = ", ".join(src["tags"])
            if src.get("repos"):
                entry["repos"] = ", ".join(src["repos"])
            if src.get("category"):
                entry["category"] = src["category"]
            if src.get("max_pages"):
                entry["max_pages"] = src["max_pages"]
            if src.get("max_depth") is not None:
                entry["max_depth"] = src["max_depth"]
            if src.get("max_issues"):
                entry["max_pages"] = src["max_issues"]
            if src.get("max_files"):
                entry["max_pages"] = src["max_files"]
            sources.append(entry)

    return NoriResponse(data={
        "target": target,
        "sources": sources,
        "total": len(sources),
    })


# ── 시스템 모니터 ──
def _get_gpu_info() -> dict:
    """GPU/VRAM 사용량 조회 (NVIDIA GPU 있을 때만)"""
    try:
        import GPUtil
        gpus = GPUtil.getGPUs()
        if gpus:
            gpu = gpus[0]  # 첫 번째 GPU
            return {
                "name": gpu.name,
                "gpu_percent": round(gpu.load * 100, 1),
                "vram_used_mb": round(gpu.memoryUsed),
                "vram_total_mb": round(gpu.memoryTotal),
                "vram_percent": round(gpu.memoryUtil * 100, 1),
                "available": True,
            }
    except Exception:
        pass
    return {"available": False, "gpu_percent": 0, "vram_percent": 0}


def _add_recent_task(name: str, task_type: str, success: bool, message: str):
    """최근 작업 기록 추가"""
    _recent_tasks.insert(0, {
        "name": name,
        "type": task_type,
        "success": success,
        "message": message,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    # 최대 20건 유지
    while len(_recent_tasks) > 20:
        _recent_tasks.pop()


@router.get("/system-info", response_model=NoriResponse)
async def system_info():
    """시스템 리소스 + 백그라운드 작업 상태"""
    # CPU / RAM
    cpu_percent = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()

    # GPU
    gpu = _get_gpu_info()

    # 실행 중인 백그라운드 작업
    running_tasks = []
    if _embed_status["running"]:
        running_tasks.append({
            "name": f"임베딩: {_embed_status['target']}",
            "type": "embed",
            "message": _embed_status["message"],
        })
    for target, info in _collect_status.items():
        if info.get("running"):
            running_tasks.append({
                "name": f"수집: {target}",
                "type": "collect",
                "message": info["message"],
                "started_at": info.get("started_at"),
            })

    return NoriResponse(data={
        "cpu_percent": cpu_percent,
        "ram_used_mb": round(mem.used / (1024 * 1024)),
        "ram_total_mb": round(mem.total / (1024 * 1024)),
        "ram_percent": mem.percent,
        "gpu": gpu,
        "running_tasks": running_tasks,
        "recent_tasks": _recent_tasks[:10],
    })


# ── 개별 소스 갱신 ──
@router.post("/refresh-sources", response_model=NoriResponse)
async def refresh_sources(req: RefreshSourcesRequest):
    """특정 타겟 내 선택한 소스만 개별 갱신 (URL 재수집)"""
    config_key = _CONFIG_KEY_MAP.get(req.target)
    if not config_key:
        return NoriResponse(success=False, error=f"알 수 없는 타겟: {req.target}")

    config = _load_config()
    target_config = config.get("targets", {}).get(config_key, {})

    # javadoc은 패키지 단위
    if req.target == "javadoc":
        base_url = target_config.get("base_url", "")
        packages = target_config.get("packages", [])
        matched = [p for p in packages if p in req.source_names]
        if not matched:
            return NoriResponse(success=False, error="일치하는 패키지가 없습니다.")

        urls = []
        for pkg in matched:
            url = f"{base_url}/{pkg.replace('.', '/')}/package-summary.html"
            urls.append({"name": pkg, "url": url})

        key = f"{req.target}:{','.join(matched)}"
        if key in _collect_status and _collect_status[key].get("running"):
            return NoriResponse(success=False, error="이미 해당 소스를 갱신 중입니다.")

        asyncio.create_task(_refresh_individual_sources(
            req.target, matched, urls,
        ))
        return NoriResponse(data={
            "started": True,
            "message": f"{len(matched)}개 패키지 갱신을 시작합니다.",
        })

    # 그 외: sources 기반 타겟
    all_sources = target_config.get("sources", [])
    matched_sources = [s for s in all_sources if s.get("name") in req.source_names]
    if not matched_sources:
        return NoriResponse(success=False, error="일치하는 소스가 없습니다.")

    urls = []
    for src in matched_sources:
        url = src.get("url") or (src.get("start_urls", [None])[0])
        if url:
            urls.append({"name": src["name"], "url": url, "max_depth": src.get("max_depth", 1), "max_pages": src.get("max_pages", 20)})

    names = [s["name"] for s in matched_sources]
    key = f"{req.target}:{','.join(names)}"
    if key in _collect_status and _collect_status[key].get("running"):
        return NoriResponse(success=False, error="이미 해당 소스를 갱신 중입니다.")

    asyncio.create_task(_refresh_individual_sources(
        req.target, names, urls,
    ))

    return NoriResponse(data={
        "started": True,
        "message": f"{len(names)}개 소스 갱신을 시작합니다: {', '.join(names)}",
    })


async def _refresh_individual_sources(target: str, names: list[str], urls: list[dict]):
    """선택한 소스의 URL을 개별적으로 재수집"""
    import hashlib
    import re as _re
    import aiohttp

    key = f"{target}:{','.join(names)}"
    _collect_status[key] = {
        "running": True,
        "message": f"{len(names)}개 소스 갱신 중...",
        "started_at": datetime.now().strftime("%H:%M:%S"),
        "finished_at": None,
        "success": None,
    }

    target_dir = _COLLECTOR_DATA / _TARGET_DIR_MAP.get(target, target)
    target_dir.mkdir(parents=True, exist_ok=True)

    total_saved = 0
    try:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for item in urls:
                url = item["url"]
                name = item["name"]
                max_depth = item.get("max_depth", 1)
                max_pages = item.get("max_pages", 20)

                _collect_status[key]["message"] = f"'{name}' 수집 중..."
                collected = []
                await _crawl_page(session, url, max_depth, max_pages,
                                  collected, set(), set())

                for page in collected:
                    url_hash = hashlib.sha256(page["url"].encode()).hexdigest()[:16]
                    filename = f"{target}_{url_hash}.json"
                    filepath = target_dir / filename

                    doc = {
                        "url": page["url"],
                        "title": page.get("title", ""),
                        "content": page["content"],
                        "source_type": target,
                        "source_name": name,
                        "category": target,
                    }
                    with open(filepath, "w", encoding="utf-8") as f:
                        json.dump(doc, ensure_ascii=False, indent=2, fp=f)
                    total_saved += 1

        finished_at = datetime.now().strftime("%H:%M:%S")
        _collect_status[key]["message"] = f"완료! {total_saved}페이지 ({finished_at})"
        _collect_status[key]["success"] = True
        _collect_status[key]["finished_at"] = finished_at
        _add_recent_task(f"{target} → {', '.join(names)}", "source-refresh", True, f"{total_saved}페이지 수집")

    except Exception as e:
        _collect_status[key]["message"] = f"에러: {e}"
        _collect_status[key]["success"] = False
        _add_recent_task(f"{target} → {', '.join(names)}", "source-refresh", False, str(e))
        logger.error(f"개별 소스 갱신 에러 ({key}): {e}", exc_info=True)
    finally:
        _collect_status[key]["running"] = False
