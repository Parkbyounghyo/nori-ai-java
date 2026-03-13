"""
기본 수집기 — 공통 HTTP 요청 + 저장 로직
모든 수집기가 이 클래스를 상속받아 사용
"""
import asyncio
import logging
import os
import json
import hashlib
from pathlib import Path
from typing import Optional

import aiohttp
import aiofiles
from tqdm import tqdm

logger = logging.getLogger("nori-collector")


class BaseCollector:
    """수집기 기본 클래스"""

    def __init__(self, output_dir: str, delay: float = 1.0,
                 max_concurrent: int = 3, timeout: int = 30,
                 retry_count: int = 3, user_agent: str = "NoriAI-Collector/1.0"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.max_concurrent = max_concurrent
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.retry_count = retry_count
        self.user_agent = user_agent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._collected_count = 0
        self._failed_count = 0
        # 이미 수집한 URL 추적 (중복 방지)
        self._progress_file = self.output_dir / "_progress.json"
        self._collected_urls: set[str] = self._load_progress()

    def _load_progress(self) -> set[str]:
        """이전 수집 진행 상태 로드 (중간에 끊겨도 이어서 수집 가능)"""
        if self._progress_file.exists():
            with open(self._progress_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("collected_urls", []))
        return set()

    def _save_progress(self):
        """수집 진행 상태 저장"""
        with open(self._progress_file, "w", encoding="utf-8") as f:
            json.dump({"collected_urls": list(self._collected_urls)}, f)

    def _url_hash(self, url: str) -> str:
        """URL을 안전한 파일명으로 변환"""
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """URL에서 HTML 가져오기 (재시도 로직 포함)"""
        if url in self._collected_urls:
            logger.debug(f"이미 수집됨, 건너뜀: {url}")
            return None

        async with self.semaphore:
            for attempt in range(1, self.retry_count + 1):
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            ct = resp.content_type or ""
                            if not ct.startswith("text/") and "html" not in ct and "xml" not in ct and "json" not in ct:
                                logger.debug(f"바이너리 컨텐츠 건너뜀 ({ct}): {url}")
                                return None
                            return await resp.text(errors="replace")
                        elif resp.status == 404:
                            logger.warning(f"404 Not Found: {url}")
                            return None
                        else:
                            logger.warning(f"HTTP {resp.status}: {url} (시도 {attempt}/{self.retry_count})")
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"요청 실패: {url} — {e} (시도 {attempt}/{self.retry_count})")

                if attempt < self.retry_count:
                    await asyncio.sleep(self.delay * attempt)

            self._failed_count += 1
            logger.error(f"최종 실패: {url}")
            return None

    async def save_raw(self, filename: str, content: str, subfolder: str = ""):
        """원본 HTML/텍스트를 파일로 저장"""
        save_dir = self.output_dir / subfolder if subfolder else self.output_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        filepath = save_dir / filename
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(content)

    async def save_parsed(self, filename: str, data: dict, subfolder: str = ""):
        """파싱된 구조화 데이터를 JSON으로 저장"""
        save_dir = self.output_dir / subfolder if subfolder else self.output_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        filepath = save_dir / filename
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))

    def mark_collected(self, url: str):
        """URL을 수집 완료로 표시"""
        self._collected_urls.add(url)
        self._collected_count += 1
        # 10개마다 진행 상태 저장
        if self._collected_count % 10 == 0:
            self._save_progress()

    async def collect(self):
        """수집 실행 (하위 클래스에서 구현)"""
        raise NotImplementedError

    async def run(self):
        """수집 전체 실행"""
        logger.info(f"수집 시작: {self.__class__.__name__}")
        headers = {"User-Agent": self.user_agent}
        connector = aiohttp.TCPConnector(limit=self.max_concurrent)
        async with aiohttp.ClientSession(
            headers=headers, timeout=self.timeout, connector=connector
        ) as session:
            await self.collect(session)

        self._save_progress()
        logger.info(
            f"수집 완료: {self._collected_count}건 성공, {self._failed_count}건 실패"
        )
