"""
전자정부프레임워크(eGovFrame) 전문 수집기
한국 공공기관 표준 프레임워크 — 공식 가이드, 공통컴포넌트, 설정 패턴 수집

수집 대상:
- eGovFrame 공식 위키/가이드
- 공통컴포넌트 사용법
- 설정 파일 패턴 (context-*.xml, web.xml 등)
- eGov 개발자 포럼/Q&A
- GitHub eGovFramework 소스코드 주석
"""
import asyncio
import json
import logging
import re
from urllib.parse import urljoin, urlparse, urlencode, quote

import aiohttp
from bs4 import BeautifulSoup

from .base import BaseCollector

logger = logging.getLogger("nori-collector")


class EgovCollector(BaseCollector):
    """전자정부프레임워크 전문 수집기"""

    def __init__(self, sources: list[dict], output_dir: str, **kwargs):
        super().__init__(output_dir=output_dir, **kwargs)
        self.sources = sources

    # ──────────────────────────────────────────────────
    #  eGov 공식 위키/가이드 수집
    # ──────────────────────────────────────────────────
    async def _collect_egov_wiki(self, session: aiohttp.ClientSession,
                                  source: dict):
        """eGov 공식 위키/가이드 페이지 BFS 수집"""
        name = source["name"]
        base_url = source["url"]
        max_depth = source.get("max_depth", 3)
        max_pages = source.get("max_pages", 300)
        category = source.get("category", "egov-guide")

        logger.info(f"eGov 위키 수집 시작: {name} ({base_url})")

        pages = await self._discover_pages(
            session, base_url, max_depth=max_depth, max_pages=max_pages
        )
        logger.info(f"  {len(pages)}개 페이지 발견")

        collected = 0
        for page_url in pages:
            if page_url in self._collected_urls:
                continue

            html = await self.fetch(session, page_url)
            if not html:
                continue

            parsed = self._parse_egov_page(html, page_url, name, category)
            if parsed and parsed.get("sections"):
                safe_name = f"{self._url_hash(page_url)}_{parsed['title'][:40]}.json"
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                await self.save_parsed(safe_name, parsed, subfolder=name)
                self.mark_collected(page_url)
                collected += 1
                logger.info(f"  ✓ {parsed['title']} ({parsed['section_count']}개 섹션)")

            await asyncio.sleep(self.delay)

        logger.info(f"eGov 위키 수집 완료: {name} ({collected}개)")

    def _parse_egov_page(self, html: str, url: str,
                          source_name: str, category: str) -> dict | None:
        """eGov 공식 위키/가이드 페이지 파싱"""
        soup = BeautifulSoup(html, "lxml")

        title_elem = soup.select_one("h1") or soup.select_one("h2") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        # 위키/가이드 콘텐츠 영역
        content = (
            soup.select_one("div.wiki-content") or
            soup.select_one("div#content") or
            soup.select_one("article") or
            soup.select_one("main") or
            soup.select_one("div.content") or
            soup.select_one("div.container") or
            soup.select_one("body")
        )
        if not content:
            return None

        sections = []
        current_heading = title
        current_text = []

        for elem in content.find_all(
            ["h1", "h2", "h3", "h4", "p", "pre", "code", "li",
             "table", "blockquote", "dl", "dt", "dd"]
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
                lang = self._detect_egov_code_lang(code)
                current_text.append(f"```{lang}\n{code}\n```")

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

            elif tag == "code" and elem.parent and elem.parent.name != "pre":
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

        # eGov 레이어 감지
        egov_layer = self._detect_egov_layer(full_text, title)

        # eGov 공통컴포넌트 감지
        common_component = self._detect_common_component(full_text, title)

        return {
            "source_type": "egov",
            "source_name": source_name,
            "category": category,
            "title": title,
            "url": url,
            "sections": sections,
            "full_text": full_text,
            "section_count": len(sections),
            "egov_layer": egov_layer,
            "common_component": common_component,
            "has_code_examples": "```" in full_text,
            "has_xml_config": "```xml" in full_text,
            "is_korean": True,
        }

    # ──────────────────────────────────────────────────
    #  eGov 개발자 Q&A 수집
    # ──────────────────────────────────────────────────
    async def _collect_egov_qna(self, session: aiohttp.ClientSession,
                                 source: dict):
        """eGov 개발자 포럼/Q&A 수집"""
        name = source["name"]
        base_url = source["url"]
        max_pages = source.get("max_pages", 100)
        category = source.get("category", "egov-qna")

        logger.info(f"eGov Q&A 수집 시작: {name}")

        # Q&A 목록 페이지 순회
        total_collected = 0
        page = 1

        while page <= max_pages:
            list_url = f"{base_url}?page={page}" if "?" not in base_url else f"{base_url}&page={page}"
            html = await self.fetch(session, list_url)
            if not html:
                break

            soup = BeautifulSoup(html, "lxml")
            article_links = []

            # 게시글 링크 추출 — eGov 사이트 구조에 맞게
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                full = urljoin(base_url, href)
                parsed = urlparse(full)
                # 게시글 상세 페이지 패턴
                if (parsed.netloc == urlparse(base_url).netloc and
                        full not in self._collected_urls and
                        any(kw in href for kw in ["/view", "/detail", "/read", "seq=", "idx="])):
                    article_links.append(full.split("#")[0])

            if not article_links:
                # 범용 패턴으로 재시도
                for link in soup.select("td a[href], li a[href], div.title a[href]"):
                    href = link.get("href", "")
                    full = urljoin(base_url, href)
                    if full not in self._collected_urls and full != base_url:
                        article_links.append(full.split("#")[0])

            if not article_links:
                break

            for article_url in set(article_links):
                if article_url in self._collected_urls:
                    continue

                html = await self.fetch(session, article_url)
                if not html:
                    continue

                parsed = self._parse_egov_qna(html, article_url, category)
                if parsed:
                    safe_name = f"egov_qna_{self._url_hash(article_url)}.json"
                    await self.save_parsed(safe_name, parsed, subfolder="egov-qna")
                    self.mark_collected(article_url)
                    total_collected += 1

                await asyncio.sleep(self.delay)

            page += 1
            await asyncio.sleep(self.delay)

        logger.info(f"eGov Q&A 수집 완료: {total_collected}개")

    def _parse_egov_qna(self, html: str, url: str, category: str) -> dict | None:
        """eGov Q&A 게시글 파싱"""
        soup = BeautifulSoup(html, "lxml")

        title_elem = soup.select_one("h1, h2, h3, div.title, span.title")
        title = title_elem.get_text(strip=True) if title_elem else ""
        if not title:
            return None

        # 질문 본문
        body_elem = (
            soup.select_one("div.content") or
            soup.select_one("div.view-content") or
            soup.select_one("div.board-content") or
            soup.select_one("article") or
            soup.select_one("td.content")
        )
        q_text = body_elem.get_text(strip=True) if body_elem else ""
        if not q_text or len(q_text) < 20:
            return None

        # 답변/댓글 영역
        answers = []
        for reply in soup.select("div.reply, div.answer, div.comment, li.comment"):
            text = reply.get_text(strip=True)
            if text and len(text) > 10:
                answers.append({"text": text[:2000]})

        # 코드 블록 추출
        code_blocks = []
        for pre in soup.find_all("pre"):
            code = pre.get_text(strip=True)
            if code and len(code) > 10:
                code_blocks.append(code)

        error_patterns = self._detect_error_patterns(q_text)
        egov_layer = self._detect_egov_layer(q_text, title)

        full_text = f"## 질문: {title}\n\n{q_text}\n\n"
        for i, ans in enumerate(answers, 1):
            full_text += f"## 답변 {i}\n{ans['text']}\n\n"

        return {
            "source_type": "egov-qa",
            "source_name": "egov-community",
            "category": category,
            "title": title,
            "url": url,
            "question_text": q_text[:3000],
            "answers": answers[:5],
            "code_blocks": code_blocks[:5],
            "error_patterns": error_patterns,
            "egov_layer": egov_layer,
            "full_text": full_text[:5000],
            "is_korean": True,
        }

    # ──────────────────────────────────────────────────
    #  eGov GitHub 소스코드 수집
    # ──────────────────────────────────────────────────
    async def _collect_egov_github(self, session: aiohttp.ClientSession,
                                    source: dict):
        """eGov GitHub 저장소에서 핵심 소스/설정 파일 수집"""
        name = source["name"]
        repos = source.get("repos", [])
        category = source.get("category", "egov-source")
        file_patterns = source.get("file_patterns", [
            ".java", ".xml", ".properties", ".yml", ".yaml"
        ])

        logger.info(f"eGov GitHub 소스 수집 시작: {name}")

        total_collected = 0
        headers = {"Accept": "application/vnd.github.v3+json"}

        for repo in repos:
            logger.info(f"  레포: {repo}")

            # 주요 디렉토리 트리 가져오기
            tree_url = f"https://api.github.com/repos/{repo}/git/trees/master?recursive=1"
            try:
                async with self.semaphore:
                    async with session.get(tree_url, headers=headers) as resp:
                        if resp.status == 200:
                            tree_data = await resp.json()
                        elif resp.status == 404:
                            # master 대신 main 시도
                            tree_url = tree_url.replace("/master?", "/main?")
                            async with session.get(tree_url, headers=headers) as resp2:
                                if resp2.status == 200:
                                    tree_data = await resp2.json()
                                else:
                                    logger.warning(f"  트리 조회 실패: {repo}")
                                    continue
                        else:
                            logger.warning(f"  HTTP {resp.status}: {tree_url}")
                            continue
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"  요청 실패: {e}")
                continue

            # 중요 파일 필터링
            important_files = []
            for item in tree_data.get("tree", []):
                if item.get("type") != "blob":
                    continue
                path = item.get("path", "")
                if any(path.endswith(ext) for ext in file_patterns):
                    # 핵심 파일 우선 (Controller, Service, DAO 등)
                    important_files.append(path)

            # 파일 수 제한
            max_files = source.get("max_files", 200)
            # 우선순위: 설정 파일 > 핵심 레이어 > 나머지
            priority_files = self._prioritize_egov_files(important_files)[:max_files]

            logger.info(f"  {len(priority_files)}개 파일 수집 대상")

            for file_path in priority_files:
                file_url = f"https://raw.githubusercontent.com/{repo}/master/{file_path}"
                if file_url in self._collected_urls:
                    continue

                content = await self.fetch(session, file_url)
                if not content:
                    # main 브랜치 시도
                    file_url = file_url.replace("/master/", "/main/")
                    content = await self.fetch(session, file_url)
                    if not content:
                        continue

                parsed = self._parse_egov_source(content, file_path, repo, category)
                if parsed:
                    safe_repo = repo.replace("/", "_")
                    safe_path = file_path.replace("/", "_")[:60]
                    safe_name = f"src_{safe_repo}_{safe_path}.json"
                    safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                    await self.save_parsed(safe_name, parsed,
                                           subfolder=f"egov-source/{safe_repo}")
                    self.mark_collected(file_url)
                    total_collected += 1

                await asyncio.sleep(self.delay * 0.5)

        logger.info(f"eGov GitHub 소스 수집 완료: {total_collected}개")

    def _prioritize_egov_files(self, files: list[str]) -> list[str]:
        """eGov 파일 우선순위 정렬"""
        priority_keywords = {
            # 최고 우선순위 — 설정 파일
            3: ["context-", "web.xml", "dispatcher-servlet", ".properties",
                "application.yml", "application.yaml", "pom.xml", "build.gradle"],
            # 높은 우선순위 — 핵심 레이어
            2: ["Controller", "Service", "ServiceImpl", "Dao", "DAO",
                "Mapper", "Vo", "VO", "impl/", "Impl"],
            # 보통 — 공통컴포넌트
            1: ["Egov", "egov", "common/", "cmm/", "util/"],
        }

        def file_priority(path: str) -> int:
            for priority, keywords in priority_keywords.items():
                if any(kw in path for kw in keywords):
                    return priority
            return 0

        return sorted(files, key=file_priority, reverse=True)

    def _parse_egov_source(self, content: str, file_path: str,
                            repo: str, category: str) -> dict | None:
        """eGov 소스코드/설정 파일 파싱"""
        if not content or len(content) < 10:
            return None

        # 파일 유형 감지
        if file_path.endswith(".java"):
            file_type = "java-source"
            lang = "java"
        elif file_path.endswith(".xml"):
            file_type = "xml-config"
            lang = "xml"
        elif file_path.endswith((".properties", ".yml", ".yaml")):
            file_type = "config"
            lang = "yaml" if file_path.endswith((".yml", ".yaml")) else "properties"
        else:
            file_type = "other"
            lang = ""

        # Java 소스에서 주석/JavaDoc 추출
        javadoc_comments = []
        annotations = []
        if file_type == "java-source":
            javadoc_comments = re.findall(r'/\*\*(.*?)\*/', content, re.DOTALL)
            annotations = re.findall(r'@(\w+(?:\([^)]*\))?)', content)

        # eGov 레이어 감지
        egov_layer = self._detect_egov_layer(content, file_path)

        return {
            "source_type": "egov-source",
            "source_name": "egov-github",
            "category": category,
            "repo": repo,
            "file_path": file_path,
            "file_type": file_type,
            "language": lang,
            "content": content[:10000],
            "javadoc_comments": javadoc_comments[:10],
            "annotations": annotations[:20],
            "egov_layer": egov_layer,
            "full_text": f"## {file_path}\n\n```{lang}\n{content[:10000]}\n```",
        }

    # ──────────────────────────────────────────────────
    #  BFS 페이지 탐색 (eGov 위키용)
    # ──────────────────────────────────────────────────
    async def _discover_pages(self, session: aiohttp.ClientSession,
                               base_url: str, max_depth: int = 3,
                               max_pages: int = 300) -> list[str]:
        """BFS로 하위 페이지 탐색"""
        discovered = set()
        to_visit = [(base_url, 0)]
        visited = set()

        while to_visit and len(discovered) < max_pages:
            url, depth = to_visit.pop(0)
            clean = url.split("#")[0].split("?")[0]
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

                full_url = urljoin(url, href).split("#")[0]
                base_parsed = urlparse(base_url)
                full_parsed = urlparse(full_url)

                if (full_parsed.netloc == base_parsed.netloc and
                        full_url not in visited and
                        len(discovered) < max_pages):
                    discovered.add(full_url)
                    if depth + 1 <= max_depth:
                        to_visit.append((full_url, depth + 1))

            await asyncio.sleep(self.delay)

        discovered.add(base_url)
        return list(discovered)

    # ──────────────────────────────────────────────────
    #  eGov 전용 유틸리티
    # ──────────────────────────────────────────────────
    def _detect_egov_code_lang(self, code: str) -> str:
        """eGov 코드 언어 감지"""
        code_lower = code.lower()
        if "<bean " in code_lower or "<beans" in code_lower or "<?xml" in code_lower:
            return "xml"
        if "public class" in code or "import " in code:
            return "java"
        if "select " in code_lower and "from " in code_lower:
            return "sql"
        if "<%@" in code or "<%=" in code or "<c:" in code:
            return "jsp"
        if "spring:" in code or "server:" in code:
            return "yaml"
        return ""

    def _detect_egov_layer(self, text: str, title: str = "") -> str:
        """eGov 표준 레이어 감지"""
        combined = f"{title} {text}".lower()

        if any(kw in combined for kw in ["controller", "requestmapping",
                                          "modelandview", "presentation"]):
            return "presentation"
        if any(kw in combined for kw in ["serviceimpl", "service impl",
                                          "egovabstractserviceimpl", "business"]):
            return "business"
        if any(kw in combined for kw in ["dao", "mapper", "ibatis",
                                          "mybatis", "sqlmap", "persistence"]):
            return "persistence"
        if any(kw in combined for kw in ["vo", "dto", "defaultvo", "data"]):
            return "data"
        if any(kw in combined for kw in ["context-", "web.xml",
                                          "dispatcher-servlet", "configuration"]):
            return "configuration"
        if any(kw in combined for kw in ["공통컴포넌트", "common component",
                                          "egovlogin", "egovbbsmanage"]):
            return "common-component"
        return "general"

    def _detect_common_component(self, text: str, title: str = "") -> str:
        """eGov 공통컴포넌트 종류 감지"""
        combined = f"{title} {text}".lower()

        components = {
            "로그인/인증": ["로그인", "login", "인증", "authentication", "egovlogincontroller"],
            "권한관리": ["권한", "authority", "authormanage", "role"],
            "게시판": ["게시판", "bbs", "board", "egovbbsmanage"],
            "공통코드": ["공통코드", "cmm", "code", "egovcmmuseservice"],
            "파일처리": ["파일", "file", "upload", "egovfilemng"],
            "메시지": ["메시지", "message", "messagesource"],
            "배치처리": ["배치", "batch", "scheduler"],
            "보안": ["보안", "security", "xss", "csrf"],
        }

        for comp_name, keywords in components.items():
            if any(kw in combined for kw in keywords):
                return comp_name
        return ""

    def _detect_error_patterns(self, text: str) -> list[str]:
        """에러 패턴 감지"""
        patterns = [
            r"(NullPointerException)", r"(ClassNotFoundException)",
            r"(NoSuchBeanDefinitionException)", r"(BeanCreationException)",
            r"(FileNotFoundException)", r"(IOException)", r"(SQLException)",
            r"(DataAccessException)", r"(LazyInitializationException)",
            r"(NoHandlerFoundException)", r"(UnsupportedOperationException)",
        ]
        found = []
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                found.extend(re.findall(pattern, text, re.IGNORECASE))
        return list(set(found))

    # ──────────────────────────────────────────────────
    #  메인 수집 실행
    # ──────────────────────────────────────────────────
    async def collect(self, session: aiohttp.ClientSession):
        """전체 eGov 소스 수집 실행"""
        for source in self.sources:
            parser_type = source.get("parser", "egov-wiki")

            if parser_type == "egov-wiki":
                await self._collect_egov_wiki(session, source)
            elif parser_type == "egov-qna":
                await self._collect_egov_qna(session, source)
            elif parser_type == "egov-github":
                await self._collect_egov_github(session, source)
            else:
                await self._collect_egov_wiki(session, source)

            self._save_progress()
