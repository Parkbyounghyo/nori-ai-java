"""
Nori AI 자료 수집기 — 메인 실행 스크립트
백그라운드에서 실행하면서 JavaDoc + Spring + UI/UX 문서를 수집한다.

사용법:
  python main.py                       # 전체 수집
  python main.py --target javadoc      # JavaDoc만
  python main.py --target spring       # Spring만
  python main.py --target web-ui       # 웹 UI/UX만 (HTML/CSS/JS/템플릿/프레임워크)
  python main.py --target desktop-ui   # 데스크탑/모바일 UI만 (Swing/JavaFX/SWT/Android)
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml

from collectors.javadoc_collector import JavaDocCollector
from collectors.spring_collector import SpringDocCollector
from collectors.web_ui_collector import WebUICollector
from collectors.desktop_ui_collector import DesktopUICollector
from collectors.community_collector import CommunityCollector
from collectors.egov_collector import EgovCollector
from collectors.database_collector import DatabaseDocCollector

# 로깅 설정
def setup_logging(log_file: str = "collector.log"):
    """파일 + 콘솔 동시 로깅"""
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logger = logging.getLogger("nori-collector")
    logger.setLevel(logging.INFO)

    # 콘솔 핸들러
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # 파일 핸들러
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


def load_config(config_path: str = "config.yaml") -> dict:
    """설정 파일 로드"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_javadoc(config: dict, settings: dict):
    """JavaDoc 수집 실행"""
    jd_config = config["javadoc"]
    collector = JavaDocCollector(
        base_url=jd_config["base_url"],
        packages=jd_config["packages"],
        output_dir=jd_config["output_dir"],
        delay=jd_config.get("delay_seconds", 1.0),
        max_concurrent=settings.get("max_concurrent", 3),
        timeout=settings.get("timeout_seconds", 30),
        retry_count=settings.get("retry_count", 3),
        user_agent=settings.get("user_agent", "NoriAI-Collector/1.0"),
    )
    await collector.run()


async def run_spring(config: dict, settings: dict):
    """Spring 문서 수집 실행"""
    sp_config = config["spring"]
    collector = SpringDocCollector(
        sources=sp_config["sources"],
        output_dir=sp_config["output_dir"],
        delay=sp_config.get("delay_seconds", 1.5),
        max_concurrent=settings.get("max_concurrent", 3),
        timeout=settings.get("timeout_seconds", 30),
        retry_count=settings.get("retry_count", 3),
        user_agent=settings.get("user_agent", "NoriAI-Collector/1.0"),
    )
    await collector.run()


async def run_web_ui(config: dict, settings: dict):
    """웹 UI/UX 문서 수집 실행"""
    ui_config = config["web_ui"]
    collector = WebUICollector(
        sources=ui_config["sources"],
        output_dir=ui_config["output_dir"],
        delay=ui_config.get("delay_seconds", 1.5),
        max_concurrent=settings.get("max_concurrent", 3),
        timeout=settings.get("timeout_seconds", 30),
        retry_count=settings.get("retry_count", 3),
        user_agent=settings.get("user_agent", "NoriAI-Collector/1.0"),
    )
    await collector.run()


async def run_desktop_ui(config: dict, settings: dict):
    """데스크탑/모바일 UI 문서 수집 실행"""
    ui_config = config["desktop_ui"]
    collector = DesktopUICollector(
        sources=ui_config["sources"],
        output_dir=ui_config["output_dir"],
        delay=ui_config.get("delay_seconds", 1.5),
        max_concurrent=settings.get("max_concurrent", 3),
        timeout=settings.get("timeout_seconds", 30),
        retry_count=settings.get("retry_count", 3),
        user_agent=settings.get("user_agent", "NoriAI-Collector/1.0"),
    )
    await collector.run()


async def run_community(config: dict, settings: dict):
    """개발자 커뮤니티 질답/에러 사례 수집 실행"""
    comm_config = config["community"]
    collector = CommunityCollector(
        sources=comm_config["sources"],
        output_dir=comm_config["output_dir"],
        delay=comm_config.get("delay_seconds", 2.0),
        max_concurrent=settings.get("max_concurrent", 3),
        timeout=settings.get("timeout_seconds", 30),
        retry_count=settings.get("retry_count", 3),
        user_agent=settings.get("user_agent", "NoriAI-Collector/1.0"),
    )
    await collector.run()


async def run_egov(config: dict, settings: dict):
    """전자정부프레임워크 수집 실행"""
    egov_config = config["egov"]
    collector = EgovCollector(
        sources=egov_config["sources"],
        output_dir=egov_config["output_dir"],
        delay=egov_config.get("delay_seconds", 2.0),
        max_concurrent=settings.get("max_concurrent", 3),
        timeout=settings.get("timeout_seconds", 30),
        retry_count=settings.get("retry_count", 3),
        user_agent=settings.get("user_agent", "NoriAI-Collector/1.0"),
    )
    await collector.run()


async def run_database(config: dict, settings: dict):
    """데이터베이스 공식 문서 수집 실행"""
    db_config = config["database"]
    collector = DatabaseDocCollector(
        sources=db_config["sources"],
        output_dir=db_config["output_dir"],
        delay=db_config.get("delay_seconds", 1.5),
        max_concurrent=settings.get("max_concurrent", 3),
        timeout=settings.get("timeout_seconds", 30),
        retry_count=settings.get("retry_count", 3),
        user_agent=settings.get("user_agent", "NoriAI-Collector/1.0"),
    )
    await collector.run()


async def main(target: str = "all"):
    """메인 수집 실행"""
    config = load_config()
    targets = config["targets"]
    settings = config.get("settings", {})

    logger = logging.getLogger("nori-collector")
    logger.info("=" * 60)
    logger.info("🍜 Nori AI 자료 수집기 시작")
    logger.info(f"   대상: {target}")
    logger.info("=" * 60)

    # 총 수집 단계 계산
    total_steps = 0
    step = 0
    if target in ("all", "javadoc"):
        total_steps += 1
    if target in ("all", "spring"):
        total_steps += 1
    if target in ("all", "web-ui"):
        total_steps += 1
    if target in ("all", "desktop-ui"):
        total_steps += 1
    if target in ("all", "community"):
        total_steps += 1
    if target in ("all", "egov"):
        total_steps += 1
    if target in ("all", "database"):
        total_steps += 1

    if target in ("all", "javadoc") and targets["javadoc"].get("enabled", True):
        step += 1
        logger.info("")
        logger.info(f"📚 [{step}/{total_steps}] Java SE 17 API 문서 수집 시작")
        logger.info("-" * 40)
        await run_javadoc(targets, settings)

    if target in ("all", "spring") and targets["spring"].get("enabled", True):
        step += 1
        logger.info("")
        logger.info(f"🌱 [{step}/{total_steps}] Spring Framework 문서 수집 시작")
        logger.info("-" * 40)
        await run_spring(targets, settings)

    if target in ("all", "web-ui") and targets.get("web_ui", {}).get("enabled", True):
        step += 1
        logger.info("")
        logger.info(f"🌐 [{step}/{total_steps}] 웹 UI/UX 기술 문서 수집 시작")
        logger.info("-" * 40)
        await run_web_ui(targets, settings)

    if target in ("all", "desktop-ui") and targets.get("desktop_ui", {}).get("enabled", True):
        step += 1
        logger.info("")
        logger.info(f"🖥️ [{step}/{total_steps}] 데스크탑/모바일 UI 문서 수집 시작")
        logger.info("-" * 40)
        await run_desktop_ui(targets, settings)

    if target in ("all", "community") and targets.get("community", {}).get("enabled", True):
        step += 1
        logger.info("")
        logger.info(f"🔥 [{step}/{total_steps}] 개발자 커뮤니티 질답/에러 사례 수집 시작")
        logger.info("-" * 40)
        await run_community(targets, settings)

    if target in ("all", "egov") and targets.get("egov", {}).get("enabled", True):
        step += 1
        logger.info("")
        logger.info(f"🏛️ [{step}/{total_steps}] 전자정부프레임워크(eGov) 수집 시작")
        logger.info("-" * 40)
        await run_egov(targets, settings)

    if target in ("all", "database") and targets.get("database", {}).get("enabled", True):
        step += 1
        logger.info("")
        logger.info(f"🗄️ [{step}/{total_steps}] 데이터베이스 공식 문서 수집 시작")
        logger.info("-" * 40)
        await run_database(targets, settings)

    logger.info("")
    logger.info("=" * 60)
    logger.info("🎉 자료 수집 완료!")
    logger.info("   수집된 데이터: nori-collector/data/ 폴더 확인")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nori AI 자료 수집기")
    parser.add_argument(
        "--target",
        choices=["all", "javadoc", "spring", "web-ui", "desktop-ui", "community", "egov", "database"],
        default="all",
        help="수집 대상 (기본: all)"
    )
    args = parser.parse_args()

    setup_logging()
    asyncio.run(main(args.target))
