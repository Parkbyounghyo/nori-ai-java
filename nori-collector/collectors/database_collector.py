"""
데이터베이스 공식 문서 수집기
MySQL, MSSQL(SQL Server), PostgreSQL, Oracle, MariaDB, SQLite, MongoDB, Redis
"""
import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .base import BaseCollector

logger = logging.getLogger("nori-collector")


class DatabaseDocCollector(BaseCollector):
    """데이터베이스 공식 문서 수집기"""

    def __init__(self, sources: list[dict], output_dir: str, **kwargs):
        super().__init__(output_dir=output_dir, **kwargs)
        self.sources = sources

    def _is_same_domain(self, url: str, base_url: str) -> bool:
        """같은 도메인+경로 하위인지 확인"""
        parsed = urlparse(url)
        base_parsed = urlparse(base_url)
        return (parsed.netloc == base_parsed.netloc and
                parsed.path.startswith(base_parsed.path))

    def _clean_url(self, url: str) -> str:
        """URL에서 앵커/쿼리 제거"""
        return url.split("#")[0].split("?")[0]

    async def _discover_pages(self, session: aiohttp.ClientSession,
                               base_url: str, max_depth: int = 2,
                               max_pages: int = 200) -> list[str]:
        """문서 사이트에서 하위 페이지 링크 탐색 (BFS)"""
        discovered = set()
        to_visit = [(base_url, 0)]
        visited = set()

        while to_visit and len(discovered) < max_pages:
            url, depth = to_visit.pop(0)
            if url in visited or depth > max_depth:
                continue
            visited.add(url)

            html = await self.fetch(session, url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            for link in soup.select("a[href]"):
                href = link.get("href", "")
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue

                full_url = urljoin(url, href)
                full_url = self._clean_url(full_url)

                if (self._is_same_domain(full_url, base_url) and
                        full_url not in visited and
                        len(discovered) < max_pages):
                    discovered.add(full_url)
                    if depth + 1 <= max_depth:
                        to_visit.append((full_url, depth + 1))

            await asyncio.sleep(self.delay)

        discovered.add(base_url)
        return list(discovered)

    def _parse_mysql_doc(self, html: str, url: str, source_name: str) -> dict | None:
        """MySQL 공식 문서 파싱"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("div#docs-body") or
            soup.select_one("div.section") or
            soup.select_one("div.simplesect") or
            soup.select_one("div#content") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "mysql-doc")

    def _parse_mssql_doc(self, html: str, url: str, source_name: str) -> dict | None:
        """MSSQL (SQL Server) Microsoft Learn 문서 파싱"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("div.content") or
            soup.select_one("main") or
            soup.select_one("article") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "mssql-doc")

    def _parse_postgresql_doc(self, html: str, url: str, source_name: str) -> dict | None:
        """PostgreSQL 공식 문서 파싱"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("div.sect1") or
            soup.select_one("div.chapter") or
            soup.select_one("div#docContent") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "postgresql-doc")

    def _parse_oracle_doc(self, html: str, url: str, source_name: str) -> dict | None:
        """Oracle Database 공식 문서 파싱"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("div.sect1") or
            soup.select_one("div#GUID") or
            soup.select_one("div.content") or
            soup.select_one("article") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "oracle-doc")

    def _parse_mariadb_doc(self, html: str, url: str, source_name: str) -> dict | None:
        """MariaDB Knowledge Base 문서 파싱"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("div.answer") or
            soup.select_one("div.node__content") or
            soup.select_one("article") or
            soup.select_one("div.content") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "mariadb-doc")

    def _parse_sqlite_doc(self, html: str, url: str, source_name: str) -> dict | None:
        """SQLite 공식 문서 파싱"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("div#content") or
            soup.select_one("div.fancy") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "sqlite-doc")

    def _parse_mongodb_doc(self, html: str, url: str, source_name: str) -> dict | None:
        """MongoDB 공식 문서 파싱"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("div.main-column") or
            soup.select_one("section.section") or
            soup.select_one("article") or
            soup.select_one("main") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "mongodb-doc")

    def _parse_redis_doc(self, html: str, url: str, source_name: str) -> dict | None:
        """Redis 공식 문서 파싱"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("article") or
            soup.select_one("div.prose") or
            soup.select_one("main") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "redis-doc")

    def _parse_generic(self, html: str, url: str, source_name: str) -> dict | None:
        """범용 파서 (fallback)"""
        soup = BeautifulSoup(html, "lxml")
        title_elem = soup.find("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content_area = (
            soup.select_one("article") or
            soup.select_one("main") or
            soup.select_one("div.content") or
            soup.select_one("body")
        )
        if not content_area:
            return None
        return self._extract_sections(content_area, title, url, source_name, "database-doc")

    def _extract_sections(self, content_area, title: str, url: str,
                          source_name: str, source_type: str) -> dict | None:
        """공통 섹션 추출 로직 — 헤딩 기준으로 구조화"""
        sections = []
        current_heading = title
        current_text = []

        for elem in content_area.find_all(
            ["h1", "h2", "h3", "h4", "p", "pre", "code", "li", "dt", "dd", "table"]
        ):
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
                code = elem.get_text()
                current_text.append(f"```\n{code}\n```")
            elif tag == "table":
                rows = []
                for tr in elem.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    rows.append(" | ".join(cells))
                if rows:
                    current_text.append("\n".join(rows))
            else:
                text = elem.get_text(strip=True)
                if text:
                    current_text.append(text)

        if current_text:
            sections.append({
                "heading": current_heading,
                "content": "\n".join(current_text)
            })

        if not sections:
            return None

        full_text = "\n\n".join(
            f"## {s['heading']}\n{s['content']}" for s in sections
        )

        return {
            "source_type": source_type,
            "source_name": source_name,
            "title": title,
            "url": url,
            "sections": sections,
            "full_text": full_text,
            "section_count": len(sections),
        }

    # 파서 이름 → 메서드 매핑
    _PARSERS = {
        "mysql": "_parse_mysql_doc",
        "mssql": "_parse_mssql_doc",
        "postgresql": "_parse_postgresql_doc",
        "oracle": "_parse_oracle_doc",
        "mariadb": "_parse_mariadb_doc",
        "sqlite": "_parse_sqlite_doc",
        "mongodb": "_parse_mongodb_doc",
        "redis": "_parse_redis_doc",
        "generic": "_parse_generic",
    }

    async def collect(self, session: aiohttp.ClientSession):
        """전체 수집 실행"""
        for source in self.sources:
            name = source["name"]
            url = source["url"]
            parser_name = source.get("parser", "generic")
            max_depth = source.get("max_depth", 2)
            max_pages = source.get("max_pages", 200)
            category = source.get("category", name)

            parse_method_name = self._PARSERS.get(parser_name, "_parse_generic")
            parse_method = getattr(self, parse_method_name)

            logger.info(f"DB 문서 수집 시작: {name} ({url})")

            # 1. 하위 페이지 탐색
            pages = await self._discover_pages(
                session, url, max_depth=max_depth, max_pages=max_pages
            )
            logger.info(f"  {len(pages)}개 페이지 발견")

            # 2. 각 페이지 수집 & 파싱
            for page_url in pages:
                if page_url in self._collected_urls:
                    continue

                html = await self.fetch(session, page_url)
                if not html:
                    continue

                parsed = parse_method(html, page_url, name)
                if parsed and parsed.get("sections"):
                    safe_name = f"{self._url_hash(page_url)}_{parsed['title'][:50]}.json"
                    safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                    await self.save_parsed(safe_name, parsed, subfolder=category)
                    self.mark_collected(page_url)
                    logger.info(f"  ✓ {parsed['title']} ({parsed['section_count']}개 섹션)")

                await asyncio.sleep(self.delay)

            logger.info(f"DB 문서 수집 완료: {name}")
