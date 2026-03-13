"""
Spring Framework / Spring Boot 공식 문서 수집기
docs.spring.io에서 레퍼런스 문서를 HTML 단위로 수집
"""
import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .base import BaseCollector

logger = logging.getLogger("nori-collector")


class SpringDocCollector(BaseCollector):
    """Spring 공식 문서 수집기"""

    def __init__(self, sources: list[dict], output_dir: str, **kwargs):
        super().__init__(output_dir=output_dir, **kwargs)
        self.sources = sources

    def _is_same_domain(self, url: str, base_url: str) -> bool:
        """같은 도메인+경로 하위인지 확인"""
        parsed = urlparse(url)
        base_parsed = urlparse(base_url)
        return (parsed.netloc == base_parsed.netloc and
                parsed.path.startswith(base_parsed.path))

    async def _discover_pages(self, session: aiohttp.ClientSession,
                               base_url: str, max_depth: int = 2) -> list[str]:
        """메인 페이지에서 하위 문서 링크 탐색"""
        discovered = set()
        to_visit = [(base_url, 0)]
        visited = set()

        while to_visit:
            url, depth = to_visit.pop(0)
            if url in visited or depth > max_depth:
                continue
            visited.add(url)

            html = await self.fetch(session, url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            # 네비게이션 / TOC 링크 수집
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue

                full_url = urljoin(url, href)
                # 앵커 제거
                full_url = full_url.split("#")[0]

                if (self._is_same_domain(full_url, base_url) and
                        full_url not in visited and
                        full_url.endswith(".html")):
                    discovered.add(full_url)
                    if depth + 1 <= max_depth:
                        to_visit.append((full_url, depth + 1))

            await asyncio.sleep(self.delay)

        # 메인 페이지도 포함
        discovered.add(base_url)
        return list(discovered)

    def _parse_spring_doc(self, html: str, url: str, source_name: str) -> dict:
        """Spring 문서 HTML → 구조화 데이터"""
        soup = BeautifulSoup(html, "lxml")

        # 제목 추출
        title = ""
        title_elem = soup.find("h1") or soup.find("title")
        if title_elem:
            title = title_elem.get_text(strip=True)

        # 메인 컨텐츠 추출 (네비게이션, 헤더, 푸터 제외)
        content_area = (
            soup.select_one("div.sect1") or
            soup.select_one("article") or
            soup.select_one("div.content") or
            soup.select_one("main") or
            soup.select_one("body")
        )

        if not content_area:
            return None

        # 코드 블록 보존하면서 텍스트 추출
        sections = []
        current_heading = title
        current_text = []

        for elem in content_area.find_all(["h1", "h2", "h3", "h4", "p", "pre", "code",
                                            "li", "dt", "dd", "table"]):
            tag = elem.name
            if tag in ("h1", "h2", "h3", "h4"):
                if current_text:
                    sections.append({
                        "heading": current_heading,
                        "content": "\n".join(current_text)
                    })
                    current_text = []
                current_heading = elem.get_text(strip=True)
            elif tag == "pre":
                # 코드 블록은 마커와 함께 보존
                code = elem.get_text()
                current_text.append(f"```\n{code}\n```")
            elif tag == "table":
                # 테이블은 간단히 텍스트로
                rows = []
                for tr in elem.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    rows.append(" | ".join(cells))
                current_text.append("\n".join(rows))
            else:
                text = elem.get_text(strip=True)
                if text:
                    current_text.append(text)

        # 마지막 섹션 저장
        if current_text:
            sections.append({
                "heading": current_heading,
                "content": "\n".join(current_text)
            })

        # 전체 텍스트 (임베딩용)
        full_text = "\n\n".join(
            f"## {s['heading']}\n{s['content']}" for s in sections
        )

        return {
            "source_type": "spring-doc",
            "source_name": source_name,
            "title": title,
            "url": url,
            "sections": sections,
            "full_text": full_text,
            "section_count": len(sections),
        }

    async def collect(self, session: aiohttp.ClientSession):
        """전체 수집 실행"""
        for source in self.sources:
            name = source["name"]
            url = source["url"]
            logger.info(f"Spring 문서 수집 시작: {name} ({url})")

            # 1. 하위 페이지 탐색
            pages = await self._discover_pages(session, url, max_depth=2)
            logger.info(f"  {len(pages)}개 페이지 발견")

            # 2. 각 페이지 수집 & 파싱
            for page_url in pages:
                if page_url in self._collected_urls:
                    continue

                html = await self.fetch(session, page_url)
                if not html:
                    continue

                parsed = self._parse_spring_doc(html, page_url, name)
                if parsed and parsed.get("sections"):
                    safe_name = f"{self._url_hash(page_url)}_{parsed['title'][:50]}.json"
                    # 파일명에 사용 불가 문자 제거
                    safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                    await self.save_parsed(safe_name, parsed, subfolder=name)
                    self.mark_collected(page_url)
                    logger.info(f"  ✓ {parsed['title']} ({parsed['section_count']}개 섹션)")

                await asyncio.sleep(self.delay)

            logger.info(f"Spring 문서 수집 완료: {name}")
