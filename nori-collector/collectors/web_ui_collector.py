"""
웹 UI/UX 기술 문서 수집기
HTML5, CSS3, JavaScript, 템플릿 엔진, JS 프레임워크, CSS 프레임워크 문서를 수집

수집 대상:
- MDN Web Docs (HTML5, CSS3, JavaScript 레퍼런스)
- Thymeleaf 공식 문서
- Freemarker 공식 문서
- React / Vue.js / Angular 공식 문서
- Bootstrap / Tailwind CSS 문서
- jQuery 문서 (eGov 기본 라이브러리)
"""
import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .base import BaseCollector

logger = logging.getLogger("nori-collector")


class WebUICollector(BaseCollector):
    """웹 UI/UX 기술 문서 수집기"""

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
            clean = self._clean_url(url)
            if clean in visited or depth > max_depth:
                continue
            visited.add(clean)

            html = await self.fetch(session, url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")

            for link in soup.select("a[href]"):
                href = link.get("href", "")
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue
                if href.startswith("mailto:"):
                    continue

                full_url = self._clean_url(urljoin(url, href))

                if (self._is_same_domain(full_url, base_url) and
                        full_url not in visited and
                        len(discovered) < max_pages):
                    discovered.add(full_url)
                    if depth + 1 <= max_depth:
                        to_visit.append((full_url, depth + 1))

            await asyncio.sleep(self.delay)

        discovered.add(base_url)
        return list(discovered)

    def _parse_mdn_doc(self, html: str, url: str, category: str) -> dict | None:
        """MDN Web Docs 페이지 파싱 (HTML/CSS/JS 레퍼런스)"""
        soup = BeautifulSoup(html, "lxml")

        title = ""
        title_elem = soup.select_one("h1") or soup.find("title")
        if title_elem:
            title = title_elem.get_text(strip=True)

        # MDN 메인 콘텐츠 영역
        content_area = (
            soup.select_one("article.main-page-content") or
            soup.select_one("article") or
            soup.select_one("main") or
            soup.select_one("div.content")
        )

        if not content_area:
            return None

        return self._extract_sections(content_area, title, url, "mdn-web-docs", category)

    def _parse_generic_doc(self, html: str, url: str,
                            source_name: str, category: str) -> dict | None:
        """범용 문서 페이지 파싱 (Thymeleaf, Freemarker, Bootstrap, jQuery 등)"""
        soup = BeautifulSoup(html, "lxml")

        title = ""
        title_elem = soup.select_one("h1") or soup.find("title")
        if title_elem:
            title = title_elem.get_text(strip=True)

        # 메인 콘텐츠 영역 탐색 (일반적인 문서 사이트 구조)
        content_area = (
            soup.select_one("article") or
            soup.select_one("main") or
            soup.select_one("div.content") or
            soup.select_one("div.container") or
            soup.select_one("div#content") or
            soup.select_one("div.documentation") or
            soup.select_one("body")
        )

        if not content_area:
            return None

        return self._extract_sections(content_area, title, url, source_name, category)

    def _extract_sections(self, content_area, title: str, url: str,
                           source_name: str, category: str) -> dict | None:
        """콘텐츠 영역에서 섹션별 텍스트 추출"""
        sections = []
        current_heading = title
        current_text = []

        for elem in content_area.find_all(
            ["h1", "h2", "h3", "h4", "p", "pre", "code", "li", "dt", "dd",
             "table", "blockquote", "dl"]
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
                # 코드 블록 보존 — 언어 힌트 추출
                code_elem = elem.find("code")
                lang_hint = ""
                if code_elem:
                    classes = code_elem.get("class", [])
                    for cls in classes:
                        if cls.startswith("language-") or cls.startswith("lang-"):
                            lang_hint = cls.split("-", 1)[1]
                            break
                code = elem.get_text()
                if lang_hint:
                    current_text.append(f"```{lang_hint}\n{code}\n```")
                else:
                    current_text.append(f"```\n{code}\n```")

            elif tag == "table":
                rows = []
                for tr in elem.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    if cells:
                        rows.append(" | ".join(cells))
                if rows:
                    current_text.append("\n".join(rows))

            elif tag == "blockquote":
                text = elem.get_text(strip=True)
                if text:
                    current_text.append(f"> {text}")

            elif tag in ("code",):
                # 인라인 코드는 부모가 pre가 아닌 경우만
                if elem.parent and elem.parent.name != "pre":
                    continue

            else:
                text = elem.get_text(strip=True)
                if text and len(text) > 3:
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
            "source_type": "web-ui",
            "source_name": source_name,
            "category": category,
            "title": title,
            "url": url,
            "sections": sections,
            "full_text": full_text,
            "section_count": len(sections),
        }

    async def _collect_source(self, session: aiohttp.ClientSession,
                               source: dict):
        """단일 소스 수집"""
        name = source["name"]
        url = source["url"]
        category = source.get("category", "web-general")
        parser_type = source.get("parser", "generic")
        max_depth = source.get("max_depth", 2)
        max_pages = source.get("max_pages", 200)

        logger.info(f"웹 UI 문서 수집 시작: {name} ({url})")

        # 1. 하위 페이지 탐색
        pages = await self._discover_pages(
            session, url, max_depth=max_depth, max_pages=max_pages
        )
        logger.info(f"  {len(pages)}개 페이지 발견")

        collected = 0
        # 2. 각 페이지 수집 & 파싱
        for page_url in pages:
            if page_url in self._collected_urls:
                continue

            html = await self.fetch(session, page_url)
            if not html:
                continue

            # 파서 선택
            if parser_type == "mdn":
                parsed = self._parse_mdn_doc(html, page_url, category)
            else:
                parsed = self._parse_generic_doc(html, page_url, name, category)

            if parsed and parsed.get("sections"):
                safe_title = parsed["title"][:50] if parsed["title"] else "untitled"
                safe_name = f"{self._url_hash(page_url)}_{safe_title}.json"
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                await self.save_parsed(safe_name, parsed, subfolder=name)
                self.mark_collected(page_url)
                collected += 1
                logger.info(f"  ✓ {parsed['title']} ({parsed['section_count']}개 섹션)")

            await asyncio.sleep(self.delay)

        logger.info(f"웹 UI 문서 수집 완료: {name} ({collected}개 수집)")

    async def collect(self, session: aiohttp.ClientSession):
        """전체 웹 UI 소스 수집 실행"""
        for source in self.sources:
            await self._collect_source(session, source)
