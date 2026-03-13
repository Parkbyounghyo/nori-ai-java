"""
개발자 커뮤니티 질답/에러 해결 사례 수집기
실전 개발자들의 경험 — 에러 해결법, 비법, 설정 팁, 패턴을 수집

수집 대상:
- StackOverflow (Java, Spring, eGov 관련 인기 질답)
- Baeldung (Java/Spring 실전 튜토리얼, 에러 해결)
- Spring.io Guides (공식 Getting Started 가이드)
- OKKY (한국 개발자 커뮤니티)
- GitHub Discussions/Issues (Spring Boot, eGov 프로젝트)
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


class CommunityCollector(BaseCollector):
    """개발자 커뮤니티 질답/에러 해결 사례 수집기"""

    def __init__(self, sources: list[dict], output_dir: str, **kwargs):
        super().__init__(output_dir=output_dir, **kwargs)
        self.sources = sources

    # ──────────────────────────────────────────────────
    #  StackOverflow 수집 (API v2.3 — 인증 불필요 공개 API)
    # ──────────────────────────────────────────────────
    async def _collect_stackoverflow(self, session: aiohttp.ClientSession,
                                      source: dict):
        """StackOverflow 인기 질답 수집 (태그 기반)"""
        name = source["name"]
        tags = source.get("tags", ["java"])
        max_pages = source.get("max_pages", 10)
        page_size = min(source.get("page_size", 30), 100)
        category = source.get("category", "community-stackoverflow")

        logger.info(f"StackOverflow 수집 시작: {name} (태그: {tags})")

        total_collected = 0

        for tag in tags:
            logger.info(f"  태그: [{tag}] 수집 중...")
            page = 1

            while page <= max_pages:
                # StackOverflow API로 인기 질문 조회
                api_url = "https://api.stackexchange.com/2.3/questions"
                params = {
                    "order": "desc",
                    "sort": "votes",
                    "tagged": tag,
                    "site": "stackoverflow",
                    "filter": "withbody",
                    "pagesize": page_size,
                    "page": page,
                }
                query_url = f"{api_url}?{urlencode(params)}"

                try:
                    async with self.semaphore:
                        async with session.get(query_url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                            elif resp.status == 429:
                                logger.warning(f"  StackOverflow API 제한 도달, {tag} 태그 건너뜀")
                                break
                            else:
                                logger.warning(f"  HTTP {resp.status}: {query_url}")
                                break
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"  요청 실패: {e}")
                    break

                questions = data.get("items", [])
                if not questions:
                    break

                for q in questions:
                    q_id = q.get("question_id")
                    q_url = f"https://stackoverflow.com/q/{q_id}"

                    if q_url in self._collected_urls:
                        continue

                    # 답변이 있는 질문만 수집 (해결된 사례)
                    if q.get("answer_count", 0) == 0:
                        continue

                    # 답변 가져오기
                    answers = await self._fetch_so_answers(session, q_id)

                    parsed = self._parse_so_question(q, answers, tag, category)
                    if parsed:
                        safe_name = f"so_{q_id}_{tag}.json"
                        await self.save_parsed(safe_name, parsed,
                                               subfolder=f"stackoverflow/{tag}")
                        self.mark_collected(q_url)
                        total_collected += 1

                        if total_collected % 20 == 0:
                            logger.info(f"  [{tag}] {total_collected}개 수집됨")

                    await asyncio.sleep(self.delay)

                # API 쿼터 보호
                if not data.get("has_more", False):
                    break
                quota = data.get("quota_remaining", 999)
                if quota < 50:
                    logger.warning(f"  API 쿼터 부족 ({quota}), 잠시 대기...")
                    await asyncio.sleep(60)

                page += 1
                await asyncio.sleep(self.delay * 2)

        logger.info(f"StackOverflow 수집 완료: {name} (총 {total_collected}개)")

    async def _fetch_so_answers(self, session: aiohttp.ClientSession,
                                 question_id: int) -> list[dict]:
        """질문에 대한 답변 가져오기 (투표순 상위 3개)"""
        api_url = f"https://api.stackexchange.com/2.3/questions/{question_id}/answers"
        params = {
            "order": "desc",
            "sort": "votes",
            "site": "stackoverflow",
            "filter": "withbody",
            "pagesize": 3,
        }
        query_url = f"{api_url}?{urlencode(params)}"

        try:
            async with self.semaphore:
                async with session.get(query_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("items", [])
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return []

    def _parse_so_question(self, question: dict, answers: list[dict],
                            tag: str, category: str) -> dict | None:
        """StackOverflow 질문+답변 파싱"""
        q_id = question.get("question_id")
        title = question.get("title", "")
        q_body = question.get("body", "")
        q_score = question.get("score", 0)
        q_tags = question.get("tags", [])
        is_answered = question.get("is_answered", False)

        # HTML → 텍스트 변환
        q_text = self._html_to_text(q_body)

        parsed_answers = []
        for ans in answers:
            a_body = ans.get("body", "")
            a_text = self._html_to_text(a_body)
            parsed_answers.append({
                "answer_id": ans.get("answer_id"),
                "score": ans.get("score", 0),
                "is_accepted": ans.get("is_accepted", False),
                "text": a_text,
            })

        if not parsed_answers:
            return None

        # 최고 답변의 핵심 코드 추출
        best_answer = parsed_answers[0]
        code_blocks = self._extract_code_blocks(answers[0].get("body", ""))

        # 에러 패턴 + 버전 감지
        combined_text = f"{title}\n{q_text}"
        for ans in parsed_answers:
            combined_text += f"\n{ans['text']}"

        error_info = self._analyze_error_detail(combined_text)
        version_info = self._detect_versions(combined_text, q_tags)

        full_text = f"## 질문: {title}\n\n{q_text}\n\n"
        for i, ans in enumerate(parsed_answers, 1):
            accepted = " ✅" if ans["is_accepted"] else ""
            full_text += f"## 답변 {i} (점수: {ans['score']}){accepted}\n\n{ans['text']}\n\n"

        return {
            "source_type": "community-qa",
            "source_name": "stackoverflow",
            "category": category,
            "question_id": q_id,
            "title": title,
            "url": f"https://stackoverflow.com/q/{q_id}",
            "tags": q_tags,
            "primary_tag": tag,
            "question_score": q_score,
            "is_answered": is_answered,
            "question_text": q_text,
            "answers": parsed_answers,
            "best_answer_code": code_blocks,
            "error_info": error_info,
            "version_info": version_info,
            "answer_count": len(parsed_answers),
            "full_text": full_text,
        }

    # ──────────────────────────────────────────────────
    #  Baeldung 튜토리얼 수집 (실전 Java/Spring 가이드)
    # ──────────────────────────────────────────────────
    async def _collect_baeldung(self, session: aiohttp.ClientSession,
                                 source: dict):
        """Baeldung 실전 튜토리얼 수집"""
        name = source["name"]
        urls = source.get("start_urls", [])
        max_pages = source.get("max_pages", 200)
        category = source.get("category", "tutorial-baeldung")

        logger.info(f"Baeldung 튜토리얼 수집 시작: {name}")

        all_pages = set()
        for start_url in urls:
            pages = await self._discover_article_links(
                session, start_url,
                max_depth=source.get("max_depth", 2),
                max_pages=max_pages // len(urls),
                domain_filter="baeldung.com"
            )
            all_pages.update(pages)

        logger.info(f"  {len(all_pages)}개 페이지 발견")

        collected = 0
        for page_url in all_pages:
            if page_url in self._collected_urls:
                continue

            html = await self.fetch(session, page_url)
            if not html:
                continue

            parsed = self._parse_baeldung_article(html, page_url, category)
            if parsed and parsed.get("sections"):
                safe_name = f"{self._url_hash(page_url)}_{parsed['title'][:40]}.json"
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                await self.save_parsed(safe_name, parsed, subfolder="baeldung")
                self.mark_collected(page_url)
                collected += 1
                logger.info(f"  ✓ {parsed['title']} ({parsed['section_count']}개 섹션)")

            await asyncio.sleep(self.delay)

        logger.info(f"Baeldung 수집 완료: {collected}개")

    def _parse_baeldung_article(self, html: str, url: str,
                                 category: str) -> dict | None:
        """Baeldung 기사 파싱 — 튜토리얼/에러 해결/패턴 구분"""
        soup = BeautifulSoup(html, "lxml")

        title_elem = soup.select_one("h1.entry-title") or soup.select_one("h1")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content = (
            soup.select_one("div.entry-content") or
            soup.select_one("article") or
            soup.select_one("div.post-content")
        )
        if not content:
            return None

        sections = []
        current_heading = title
        current_text = []

        for elem in content.find_all(
            ["h2", "h3", "h4", "p", "pre", "li", "table", "blockquote"]
        ):
            tag = elem.name

            if tag in ("h2", "h3", "h4"):
                if current_text:
                    sections.append({
                        "heading": current_heading,
                        "content": "\n".join(current_text)
                    })
                    current_text = []
                current_heading = elem.get_text(strip=True)
            elif tag == "pre":
                code = elem.get_text()
                lang = self._detect_code_language(code)
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

        # 에러/버전 상세 분석
        error_info = self._analyze_error_detail(full_text)
        version_info = self._detect_versions(full_text)

        article_type = "error-solution" if error_info["is_error_related"] else "tutorial"
        if error_info["is_migration_related"]:
            article_type = "migration-guide"
        elif any(kw in title.lower() for kw in ["pattern", "design", "singleton", "factory"]):
            article_type = "design-pattern"

        return {
            "source_type": "community-tutorial",
            "source_name": "baeldung",
            "category": category,
            "article_type": article_type,
            "title": title,
            "url": url,
            "sections": sections,
            "full_text": full_text,
            "section_count": len(sections),
            "error_info": error_info,
            "version_info": version_info,
            "has_code_examples": "```" in full_text,
        }

    # ──────────────────────────────────────────────────
    #  Spring.io Getting Started 가이드 수집
    # ──────────────────────────────────────────────────
    async def _collect_spring_guides(self, session: aiohttp.ClientSession,
                                      source: dict):
        """Spring.io 공식 실전 가이드 수집"""
        name = source["name"]
        base_url = source.get("url", "https://spring.io/guides")
        category = source.get("category", "tutorial-spring")

        logger.info(f"Spring Guides 수집 시작: {name}")

        # 가이드 목록 페이지에서 링크 추출
        html = await self.fetch(session, base_url)
        if not html:
            return

        soup = BeautifulSoup(html, "lxml")
        guide_links = set()

        for link in soup.select("a[href]"):
            href = link.get("href", "")
            full = urljoin(base_url, href)
            if "/guides/" in full and full != base_url:
                guide_links.add(self._clean_url(full))

        logger.info(f"  {len(guide_links)}개 가이드 발견")

        collected = 0
        for guide_url in guide_links:
            if guide_url in self._collected_urls:
                continue

            html = await self.fetch(session, guide_url)
            if not html:
                continue

            parsed = self._parse_generic_article(html, guide_url, "spring-guides", category)
            if parsed and parsed.get("sections"):
                safe_name = f"{self._url_hash(guide_url)}_{parsed['title'][:40]}.json"
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                await self.save_parsed(safe_name, parsed, subfolder="spring-guides")
                self.mark_collected(guide_url)
                collected += 1
                logger.info(f"  ✓ {parsed['title']}")

            await asyncio.sleep(self.delay)

        logger.info(f"Spring Guides 수집 완료: {collected}개")

    # ──────────────────────────────────────────────────
    #  OKKY 한국 개발자 커뮤니티 수집
    # ──────────────────────────────────────────────────
    async def _collect_okky(self, session: aiohttp.ClientSession,
                             source: dict):
        """OKKY 한국 개발자 커뮤니티 인기 글 수집"""
        name = source["name"]
        base_url = source.get("url", "https://okky.kr")
        max_pages = source.get("max_pages", 50)
        category = source.get("category", "community-okky")
        search_tags = source.get("tags", ["java", "spring", "전자정부", "egovframework"])

        logger.info(f"OKKY 커뮤니티 수집 시작: {name}")

        total_collected = 0

        for tag in search_tags:
            logger.info(f"  OKKY 검색: [{tag}]")

            # OKKY는 일반 웹 크롤링 — Q&A 페이지 탐색
            page = 1
            while page <= max_pages:
                list_url = f"{base_url}/articles/questions?query={quote(tag)}&page={page}"
                html = await self.fetch(session, list_url)
                if not html:
                    break

                soup = BeautifulSoup(html, "lxml")
                article_links = []

                for link in soup.select("a[href*='/articles/']"):
                    href = link.get("href", "")
                    full = urljoin(base_url, href)
                    if "/articles/" in full and full not in self._collected_urls:
                        article_links.append(self._clean_url(full))

                if not article_links:
                    break

                for article_url in set(article_links):
                    if article_url in self._collected_urls:
                        continue

                    html = await self.fetch(session, article_url)
                    if not html:
                        continue

                    parsed = self._parse_okky_article(html, article_url, tag, category)
                    if parsed:
                        safe_name = f"okky_{self._url_hash(article_url)}_{tag}.json"
                        safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                        await self.save_parsed(safe_name, parsed,
                                               subfolder=f"okky/{tag}")
                        self.mark_collected(article_url)
                        total_collected += 1

                    await asyncio.sleep(self.delay)

                page += 1
                await asyncio.sleep(self.delay)

        logger.info(f"OKKY 수집 완료: {total_collected}개")

    def _parse_okky_article(self, html: str, url: str,
                             tag: str, category: str) -> dict | None:
        """OKKY 게시글 파싱 (질문 + 답변/댓글)"""
        soup = BeautifulSoup(html, "lxml")

        title_elem = soup.select_one("h1") or soup.select_one("h2.title")
        title = title_elem.get_text(strip=True) if title_elem else ""
        if not title:
            return None

        # 질문 본문
        q_body = soup.select_one("div.content-body") or soup.select_one("article")
        q_text = q_body.get_text(strip=True) if q_body else ""

        # 답변/댓글
        answers = []
        for comment in soup.select("div.comment, div.answer, div.reply"):
            text = comment.get_text(strip=True)
            if text and len(text) > 10:
                answers.append({"text": text[:2000]})

        full_text = f"## 질문: {title}\n\n{q_text}\n\n"
        for i, ans in enumerate(answers, 1):
            full_text += f"## 답변 {i}\n{ans['text']}\n\n"

        error_info = self._analyze_error_detail(full_text)
        version_info = self._detect_versions(full_text, [tag])

        return {
            "source_type": "community-qa",
            "source_name": "okky",
            "category": category,
            "title": title,
            "url": url,
            "tags": [tag],
            "question_text": q_text[:3000],
            "answers": answers[:5],
            "error_info": error_info,
            "version_info": version_info,
            "full_text": full_text[:5000],
            "is_korean": True,
        }

    # ──────────────────────────────────────────────────
    #  GitHub Issues/Discussions 수집
    # ──────────────────────────────────────────────────
    async def _collect_github_issues(self, session: aiohttp.ClientSession,
                                      source: dict):
        """GitHub 프로젝트 이슈/디스커션에서 에러 해결 사례 수집"""
        name = source["name"]
        repos = source.get("repos", [])
        max_issues = source.get("max_issues", 100)
        category = source.get("category", "community-github")

        logger.info(f"GitHub Issues 수집 시작: {name}")

        total_collected = 0
        headers = {
            "Accept": "application/vnd.github.v3+json",
        }

        for repo in repos:
            logger.info(f"  레포: {repo}")

            # 가장 코멘트 많은 이슈 = 활발한 토론 = 유용한 정보
            page = 1
            collected_repo = 0

            while collected_repo < max_issues and page <= 10:
                api_url = f"https://api.github.com/repos/{repo}/issues"
                params = {
                    "state": "closed",
                    "sort": "comments",
                    "direction": "desc",
                    "per_page": 30,
                    "page": page,
                }
                query_url = f"{api_url}?{urlencode(params)}"

                try:
                    async with self.semaphore:
                        async with session.get(query_url, headers=headers) as resp:
                            if resp.status == 200:
                                issues = await resp.json()
                            elif resp.status == 403:
                                logger.warning(f"  GitHub API 제한, {repo} 건너뜀")
                                break
                            else:
                                logger.warning(f"  HTTP {resp.status}: {query_url}")
                                break
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"  요청 실패: {e}")
                    break

                if not issues:
                    break

                for issue in issues:
                    # Pull Request 제외
                    if "pull_request" in issue:
                        continue

                    issue_url = issue.get("html_url", "")
                    if issue_url in self._collected_urls:
                        continue

                    # 코멘트가 있는 이슈만 (해결 사례)
                    if issue.get("comments", 0) == 0:
                        continue

                    # 코멘트 가져오기
                    comments = await self._fetch_gh_comments(
                        session, issue.get("comments_url", ""), headers
                    )

                    parsed = self._parse_github_issue(issue, comments, repo, category)
                    if parsed:
                        safe_repo = repo.replace("/", "_")
                        safe_name = f"gh_{safe_repo}_{issue['number']}.json"
                        await self.save_parsed(safe_name, parsed,
                                               subfolder=f"github/{safe_repo}")
                        self.mark_collected(issue_url)
                        total_collected += 1
                        collected_repo += 1

                    await asyncio.sleep(self.delay)

                page += 1
                await asyncio.sleep(self.delay * 2)

        logger.info(f"GitHub Issues 수집 완료: {total_collected}개")

    async def _fetch_gh_comments(self, session: aiohttp.ClientSession,
                                  comments_url: str, headers: dict) -> list[dict]:
        """GitHub 이슈 코멘트 가져오기 (상위 5개)"""
        if not comments_url:
            return []
        try:
            async with self.semaphore:
                async with session.get(
                    f"{comments_url}?per_page=5", headers=headers
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return []

    def _parse_github_issue(self, issue: dict, comments: list[dict],
                             repo: str, category: str) -> dict | None:
        """GitHub 이슈+코멘트 파싱"""
        title = issue.get("title", "")
        body = issue.get("body", "") or ""
        labels = [l.get("name", "") for l in issue.get("labels", [])]

        parsed_comments = []
        for c in comments:
            c_body = c.get("body", "") or ""
            if len(c_body) > 20:
                parsed_comments.append({
                    "user": c.get("user", {}).get("login", ""),
                    "text": c_body[:2000],
                    "reactions": c.get("reactions", {}).get("total_count", 0),
                })

        full_text = f"## Issue: {title}\n\n{body}\n\n"
        for i, c in enumerate(parsed_comments, 1):
            full_text += f"## Comment {i} (by {c['user']})\n{c['text']}\n\n"

        error_info = self._analyze_error_detail(full_text)
        version_info = self._detect_versions(full_text, labels)

        return {
            "source_type": "community-issue",
            "source_name": "github",
            "category": category,
            "repo": repo,
            "issue_number": issue.get("number"),
            "title": title,
            "url": issue.get("html_url", ""),
            "labels": labels,
            "state": issue.get("state", ""),
            "question_text": body[:3000],
            "comments": parsed_comments,
            "error_info": error_info,
            "version_info": version_info,
            "full_text": full_text[:5000],
        }

    # ──────────────────────────────────────────────────
    #  범용 웹 기사/문서 수집 (Baeldung, Spring Guides 등에서 공유)
    # ──────────────────────────────────────────────────
    async def _discover_article_links(self, session: aiohttp.ClientSession,
                                       start_url: str, max_depth: int = 2,
                                       max_pages: int = 200,
                                       domain_filter: str = "") -> set[str]:
        """문서/블로그 사이트에서 아티클 링크 BFS 탐색"""
        discovered = set()
        to_visit = [(start_url, 0)]
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

                full_url = self._clean_url(urljoin(url, href))
                parsed = urlparse(full_url)

                if domain_filter and domain_filter not in parsed.netloc:
                    continue

                if full_url not in visited and len(discovered) < max_pages:
                    discovered.add(full_url)
                    if depth + 1 <= max_depth:
                        to_visit.append((full_url, depth + 1))

            await asyncio.sleep(self.delay)

        discovered.add(start_url)
        return discovered

    def _parse_generic_article(self, html: str, url: str,
                                source_name: str, category: str) -> dict | None:
        """범용 기사/튜토리얼 파싱"""
        soup = BeautifulSoup(html, "lxml")

        title_elem = soup.select_one("h1") or soup.find("title")
        title = title_elem.get_text(strip=True) if title_elem else ""

        content = (
            soup.select_one("article") or
            soup.select_one("main") or
            soup.select_one("div.content") or
            soup.select_one("div.container") or
            soup.select_one("div#content") or
            soup.select_one("body")
        )
        if not content:
            return None

        sections = []
        current_heading = title
        current_text = []

        for elem in content.find_all(
            ["h1", "h2", "h3", "h4", "p", "pre", "li", "table", "blockquote"]
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
                lang = self._detect_code_language(code)
                current_text.append(f"```{lang}\n{code}\n```")
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
        error_info = self._analyze_error_detail(full_text)
        version_info = self._detect_versions(full_text)

        return {
            "source_type": "community-tutorial",
            "source_name": source_name,
            "category": category,
            "title": title,
            "url": url,
            "sections": sections,
            "full_text": full_text,
            "section_count": len(sections),
            "error_info": error_info,
            "version_info": version_info,
            "has_code_examples": "```" in full_text,
        }

    # ──────────────────────────────────────────────────
    #  범용 파서 (generic — config에서 parser: generic인 것)
    # ──────────────────────────────────────────────────
    async def _collect_generic(self, session: aiohttp.ClientSession,
                                source: dict):
        """범용 웹사이트 수집 (BFS → 파싱 → 저장)"""
        name = source["name"]
        url = source["url"]
        category = source.get("category", "community-generic")
        max_depth = source.get("max_depth", 2)
        max_pages = source.get("max_pages", 100)

        logger.info(f"웹 문서 수집 시작: {name} ({url})")

        domain = urlparse(url).netloc
        pages = await self._discover_article_links(
            session, url, max_depth=max_depth,
            max_pages=max_pages, domain_filter=domain
        )
        logger.info(f"  {len(pages)}개 페이지 발견")

        collected = 0
        for page_url in pages:
            if page_url in self._collected_urls:
                continue

            html = await self.fetch(session, page_url)
            if not html:
                continue

            parsed = self._parse_generic_article(html, page_url, name, category)
            if parsed and parsed.get("sections"):
                safe_name = f"{self._url_hash(page_url)}_{parsed['title'][:40]}.json"
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
                await self.save_parsed(safe_name, parsed, subfolder=name)
                self.mark_collected(page_url)
                collected += 1
                logger.info(f"  ✓ {parsed['title']}")

            await asyncio.sleep(self.delay)

        logger.info(f"웹 문서 수집 완료: {name} ({collected}개)")

    # ──────────────────────────────────────────────────
    #  공통 유틸리티
    # ──────────────────────────────────────────────────
    def _clean_url(self, url: str) -> str:
        """URL 정규화"""
        return url.split("#")[0].split("?")[0].rstrip("/")

    def _html_to_text(self, html_body: str) -> str:
        """HTML → 구조화 텍스트 (코드 블록 보존)"""
        soup = BeautifulSoup(html_body, "lxml")
        parts = []

        for elem in soup.find_all(["p", "pre", "code", "li", "h1", "h2", "h3", "blockquote"]):
            tag = elem.name
            if tag == "pre":
                code = elem.get_text()
                lang = self._detect_code_language(code)
                parts.append(f"```{lang}\n{code}\n```")
            elif tag == "code" and elem.parent and elem.parent.name != "pre":
                parts.append(f"`{elem.get_text()}`")
            elif tag in ("h1", "h2", "h3"):
                parts.append(f"\n### {elem.get_text(strip=True)}\n")
            elif tag == "blockquote":
                parts.append(f"> {elem.get_text(strip=True)}")
            else:
                text = elem.get_text(strip=True)
                if text:
                    parts.append(text)

        return "\n\n".join(parts)

    def _extract_code_blocks(self, html_body: str) -> list[str]:
        """HTML에서 코드 블록만 추출"""
        soup = BeautifulSoup(html_body, "lxml")
        blocks = []
        for pre in soup.find_all("pre"):
            code = pre.get_text(strip=True)
            if code and len(code) > 10:
                blocks.append(code)
        return blocks

    def _detect_code_language(self, code: str) -> str:
        """코드 언어 자동 감지 (간단한 휴리스틱)"""
        code_lower = code.lower()
        if "public class" in code or "import java" in code or "void main" in code:
            return "java"
        if "def " in code and ("self" in code or "import " in code_lower):
            return "python"
        if "function " in code or "const " in code or "=>" in code:
            return "javascript"
        if "<xml" in code_lower or "<?xml" in code_lower or "xmlns" in code:
            return "xml"
        if "select " in code_lower and "from " in code_lower:
            return "sql"
        if "<dependency>" in code or "<groupId>" in code:
            return "xml"
        if "spring:" in code or "server:" in code:
            return "yaml"
        return ""

    # ──────────────────────────────────────────────────
    #  에러 상세 분석 시스템 (강화판)
    # ──────────────────────────────────────────────────

    # Java 에러/예외 카테고리별 분류
    _ERROR_CATEGORIES = {
        "runtime-exception": [
            "NullPointerException", "ArrayIndexOutOfBoundsException",
            "StringIndexOutOfBoundsException", "NumberFormatException",
            "ArithmeticException", "ClassCastException",
            "IllegalArgumentException", "IllegalStateException",
            "UnsupportedOperationException", "ConcurrentModificationException",
            "IndexOutOfBoundsException", "NegativeArraySizeException",
            "SecurityException", "NoSuchElementException",
        ],
        "checked-exception": [
            "IOException", "FileNotFoundException", "EOFException",
            "SocketException", "ConnectException", "UnknownHostException",
            "MalformedURLException", "URISyntaxException",
            "InterruptedException", "TimeoutException",
            "ParseException", "CloneNotSupportedException",
            "ReflectiveOperationException", "InstantiationException",
            "InvocationTargetException",
        ],
        "error": [
            "StackOverflowError", "OutOfMemoryError",
            "NoClassDefFoundError", "ExceptionInInitializerError",
            "ClassFormatError", "UnsatisfiedLinkError",
            "VerifyError", "LinkageError",
        ],
        "sql-db": [
            "SQLException", "SQLSyntaxErrorException", "SQLTimeoutException",
            "DataTruncation", "BatchUpdateException",
            "SQLIntegrityConstraintViolationException",
            "ORA-[0-9]+",  # Oracle 에러 코드
            "DataAccessException", "BadSqlGrammarException",
            "DuplicateKeyException", "CannotGetJdbcConnectionException",
            "DeadlockLoserDataAccessException",
        ],
        "spring-framework": [
            "NoSuchBeanDefinitionException", "BeanCreationException",
            "BeanCurrentlyInCreationException", "BeanDefinitionStoreException",
            "UnsatisfiedDependencyException", "NoUniqueBeanDefinitionException",
            "BeanInitializationException", "BeanNotOfRequiredTypeException",
            "HttpMessageNotReadableException", "HttpMessageNotWritableException",
            "MethodArgumentNotValidException", "MethodArgumentTypeMismatchException",
            "MissingServletRequestParameterException", "MissingPathVariableException",
            "HttpRequestMethodNotSupportedException",
            "HttpMediaTypeNotSupportedException",
            "NoHandlerFoundException", "ResponseStatusException",
            "BindException", "TypeMismatchException",
            "ConversionNotSupportedException",
        ],
        "spring-security": [
            "AccessDeniedException", "AuthenticationException",
            "BadCredentialsException", "InsufficientAuthenticationException",
            "UsernameNotFoundException", "AccountExpiredException",
            "CredentialsExpiredException", "LockedException",
            "DisabledException", "InvalidCsrfTokenException",
        ],
        "jpa-hibernate": [
            "LazyInitializationException", "HibernateException",
            "PersistenceException", "TransactionException",
            "OptimisticLockException", "StaleObjectStateException",
            "ConstraintViolationException", "PropertyValueException",
            "MappingException", "QueryException",
            "ObjectNotFoundException", "NonUniqueResultException",
            "EntityNotFoundException", "EntityExistsException",
            "TransactionRequiredException",
            "MultipleBagFetchException",
            "AnnotationException",
        ],
        "servlet-web": [
            "ServletException", "JspException",
            "404 Not Found", "500 Internal Server Error",
            "403 Forbidden", "405 Method Not Allowed",
            "415 Unsupported Media Type", "400 Bad Request",
            "CORS error", "Cross-Origin", "XMLHttpRequest",
        ],
        "build-deploy": [
            "Compilation error", "Build failed", "Build failure",
            "Cannot resolve symbol", "package does not exist",
            "ClassNotFoundException at deploy",
            "jar hell", "dependency conflict",
            "maven-compiler-plugin", "Could not resolve dependencies",
            "IncompatibleClassChangeError",
        ],
        "version-migration": [
            "UnsupportedClassVersionError", "class file version",
            "source release", "target release",
            "module-info", "module system",
            "javax to jakarta", "javax.servlet",
            "jakarta.servlet", "Java EE to Jakarta EE",
            "removed API", "deprecated API",
            "java.lang.reflect.InaccessibleObjectException",
            "--add-opens", "--add-modules",
            "illegal reflective access",
        ],
        "oracle-db": [
            "ORA-00001",  # unique constraint violated
            "ORA-00904",  # invalid identifier
            "ORA-00907",  # missing right parenthesis
            "ORA-00911",  # invalid character
            "ORA-00913",  # too many values
            "ORA-00918",  # column ambiguously defined
            "ORA-00923",  # FROM keyword not found
            "ORA-00933",  # SQL command not properly ended
            "ORA-00936",  # missing expression
            "ORA-00942",  # table or view does not exist
            "ORA-01000",  # maximum open cursors exceeded
            "ORA-01017",  # invalid username/password
            "ORA-01031",  # insufficient privileges
            "ORA-01400",  # cannot insert NULL
            "ORA-01403",  # no data found
            "ORA-01422",  # exact fetch returns more than requested
            "ORA-01438",  # value larger than specified precision
            "ORA-01461",  # can bind a LONG value only for insert
            "ORA-01722",  # invalid number
            "ORA-01747",  # invalid column reference
            "ORA-01756",  # quoted string not properly terminated
            "ORA-01830",  # date format picture ends before converting
            "ORA-01843",  # not a valid month
            "ORA-01861",  # literal does not match format string
            "ORA-02049",  # timeout - distributed transaction waiting for lock
            "ORA-02291",  # integrity constraint violated - parent key not found
            "ORA-02292",  # integrity constraint violated - child record found
            "ORA-04031",  # unable to allocate shared memory
            "ORA-06502",  # PL/SQL: numeric or value error
            "ORA-06550",  # PL/SQL: Statement ignored
            "ORA-12154",  # TNS:could not resolve the connect identifier
            "ORA-12170",  # TNS:Connect timeout occurred
            "ORA-12514",  # TNS:listener does not currently know of service
            "ORA-12541",  # TNS:no listener
            "ORA-12560",  # TNS:protocol adapter error
            "ORA-00054",  # resource busy and acquire with NOWAIT specified
            "ORA-00060",  # deadlock detected
            "ORA-01555",  # snapshot too old
            "ORA-04091",  # table is mutating
            "ORA-06512",  # PL/SQL backtrace
        ],
    }

    def _analyze_error_detail(self, text: str) -> dict:
        """에러 상세 분석 — 카테고리, 심각도, 스택트레이스 파싱"""
        errors_found = []
        categories_found = set()

        for category, patterns in self._ERROR_CATEGORIES.items():
            for pattern in patterns:
                if re.search(re.escape(pattern) if not pattern.startswith("ORA-") else pattern,
                             text, re.IGNORECASE):
                    errors_found.append({"error": pattern, "category": category})
                    categories_found.add(category)

        # 스택트레이스 추출
        stacktraces = self._extract_stacktraces(text)

        # Oracle ORA- 에러코드 직접 추출
        ora_codes = re.findall(r'(ORA-\d{5})', text)

        # 에러 심각도 판단
        severity = self._classify_error_severity(errors_found, text)

        # 해결책 힌트 감지 (답변에서)
        solution_hints = self._extract_solution_hints(text)

        return {
            "errors": errors_found,
            "categories": list(categories_found),
            "ora_codes": list(set(ora_codes)),
            "stacktraces": stacktraces[:3],
            "severity": severity,
            "solution_hints": solution_hints,
            "is_error_related": len(errors_found) > 0,
            "is_migration_related": "version-migration" in categories_found,
            "is_oracle_related": "oracle-db" in categories_found or bool(ora_codes),
        }

    def _extract_stacktraces(self, text: str) -> list[str]:
        """Java 스택트레이스 추출"""
        traces = []
        # "at com.xxx.xxx.Method(File.java:123)" 형태
        trace_pattern = re.compile(
            r'(?:(?:Exception|Error|Caused by)[^\n]*\n)?'
            r'(?:\s*at\s+[\w.$]+\([^)]*\)\s*\n?){2,}',
            re.MULTILINE
        )
        for match in trace_pattern.finditer(text):
            trace = match.group().strip()
            if len(trace) > 30:
                traces.append(trace[:1000])
        return traces

    def _classify_error_severity(self, errors: list[dict], text: str) -> str:
        """에러 심각도 분류"""
        if not errors:
            return "none"

        categories = {e["category"] for e in errors}

        # 치명적: OOM, StackOverflow, 빌드 실패
        if categories & {"error", "build-deploy"}:
            return "critical"
        # 높음: DB 에러, 보안, 버전 마이그레이션
        if categories & {"sql-db", "oracle-db", "spring-security", "version-migration"}:
            return "high"
        # 중간: Spring 프레임워크, JPA
        if categories & {"spring-framework", "jpa-hibernate", "servlet-web"}:
            return "medium"
        # 낮음: 일반 런타임 예외
        return "low"

    def _extract_solution_hints(self, text: str) -> list[str]:
        """답변에서 해결책 힌트 추출"""
        solution_indicators = [
            r"(?:해결|solution|fix|solved|resolve|answer)[:\s]+(.*?)(?:\n|$)",
            r"(?:try|시도)[:\s]+(.*?)(?:\n|$)",
            r"(?:change|변경|수정)[:\s]+.*?(?:to|을|를)\s+(.*?)(?:\n|$)",
            r"(?:add|추가)[:\s]+(.*?)(?:\n|$)",
            r"(?:replace|교체|바꿔)[:\s]+(.*?)(?:\n|$)",
            r"(?:remove|제거|삭제)[:\s]+(.*?)(?:\n|$)",
            r"(?:upgrade|downgrade|update)[:\s]+(.*?)(?:\n|$)",
        ]
        hints = []
        for pattern in solution_indicators:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for m in matches:
                m = m.strip()
                if m and len(m) > 5 and len(m) < 200:
                    hints.append(m)
        return hints[:10]

    # ──────────────────────────────────────────────────
    #  버전 감지 시스템
    # ──────────────────────────────────────────────────
    def _detect_versions(self, text: str, tags: list[str] = None) -> dict:
        """텍스트와 태그에서 기술 버전 정보 추출"""
        tags = tags or []
        combined = f"{text} {' '.join(tags)}"

        versions = {
            "java": self._find_java_version(combined),
            "spring_boot": self._find_version(combined, [
                r"Spring\s*Boot\s*([\d.]+)", r"spring-boot[:\s]*([\d.]+)",
                r"spring-boot-starter[:\s]*([\d.]+)",
            ]),
            "spring_framework": self._find_version(combined, [
                r"Spring\s*(?:Framework)?\s*([\d.]+\.RELEASE)",
                r"spring-(?:core|context|webmvc)[:\s]*([\d.]+)",
            ]),
            "oracle_db": self._find_version(combined, [
                r"Oracle\s*(?:Database|DB)?\s*([\d]+[cCgGiI]?(?:[\s]*Release\s*[\d.]+)?)",
                r"Oracle\s*(1[12890][cCgGiI])", r"Oracle\s*(19c|21c|23ai|23c)",
                r"ojdbc(\d+)", r"oracle\.jdbc.*?(\d+\.\d+)",
            ]),
            "mybatis": self._find_version(combined, [
                r"MyBatis\s*([\d.]+)", r"mybatis[:\s]*([\d.]+)",
                r"mybatis-spring[:\s]*([\d.]+)",
            ]),
            "hibernate": self._find_version(combined, [
                r"Hibernate\s*([\d.]+)", r"hibernate-core[:\s]*([\d.]+)",
            ]),
            "tomcat": self._find_version(combined, [
                r"Tomcat\s*([\d.]+)", r"apache-tomcat-([\d.]+)",
            ]),
            "jdk_vendor": self._find_match(combined, [
                r"(OpenJDK|Oracle\s*JDK|AdoptOpenJDK|Corretto|Zulu|GraalVM|Temurin)",
            ]),
            "egov_version": self._find_version(combined, [
                r"eGov(?:Frame(?:work)?)?\s*(?:v|version)?\s*([\d.]+)",
                r"전자정부(?:\s*프레임워크)?\s*([\d.]+)",
            ]),
        }

        # 태그에서 버전 보강
        for t in tags:
            if re.match(r"java-(\d+)", t):
                versions["java"] = re.match(r"java-(\d+)", t).group(1)
            if re.match(r"spring-boot-(\d)", t):
                versions["spring_boot"] = re.match(r"spring-boot-(\d[\d.]*)", t).group(1)

        # 마이그레이션 관련 감지
        migration = self._detect_migration(combined)
        versions["migration"] = migration

        # None 제거
        versions = {k: v for k, v in versions.items() if v}

        return versions

    def _find_java_version(self, text: str) -> str | None:
        """Java 버전 세밀 감지"""
        patterns = [
            r"Java\s*(?:SE\s*)?(\d+)(?:\.\d+)?",
            r"JDK\s*(\d+)", r"jdk(\d+)",
            r"java\.version[=:\s]+[\"']?(\d+)",
            r"source(?:Compatibility)?\s*[=:]\s*[\"']?(\d+)",
            r"target(?:Compatibility)?\s*[=:]\s*[\"']?(\d+)",
            r"--release\s+(\d+)",
            r"<java\.version>(\d+)</java\.version>",
            r"<maven\.compiler\.(?:source|target)>(\d+)",
            r"1\.([5678])\b",  # Java 1.5~1.8 → 5~8
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                ver = m.group(1)
                # 1.8 → 8 변환
                if ver in ("5", "6", "7", "8") and "1." in m.group(0):
                    return ver
                return ver
        return None

    def _find_version(self, text: str, patterns: list[str]) -> str | None:
        """패턴 목록에서 첫 번째 매칭 버전 반환"""
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    def _find_match(self, text: str, patterns: list[str]) -> str | None:
        """패턴 목록에서 첫 번째 매칭 문자열 반환"""
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    def _detect_migration(self, text: str) -> dict | None:
        """버전 마이그레이션 관련 감지"""
        migration_patterns = [
            # Java 버전 마이그레이션
            (r"(?:Java|JDK)\s*(\d+)\s*(?:to|→|->|에서)\s*(?:Java|JDK)?\s*(\d+)",
             "java"),
            (r"(?:migrate|migration|upgrade|마이그레이션|업그레이드).*?(?:Java|JDK)\s*(\d+).*?(\d+)",
             "java"),
            # Spring Boot 마이그레이션
            (r"Spring\s*Boot\s*([\d.]+)\s*(?:to|→|->)\s*([\d.]+)",
             "spring-boot"),
            # javax → jakarta 마이그레이션
            (r"javax\s*(?:to|→|->)\s*jakarta",
             "javax-to-jakarta"),
            # Oracle 버전 마이그레이션
            (r"Oracle\s*([\d]+[cCgGiI]?)\s*(?:to|→|->|에서)\s*Oracle?\s*([\d]+[cCgGiI]?)",
             "oracle"),
        ]

        for pattern, mig_type in migration_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                groups = m.groups()
                return {
                    "type": mig_type,
                    "from_version": groups[0] if len(groups) > 0 else "",
                    "to_version": groups[1] if len(groups) > 1 else "",
                }
        return None

    def _detect_error_patterns(self, text: str) -> list[str]:
        """텍스트에서 에러 패턴 감지 (하위 호환용 — 간단한 리스트 반환)"""
        info = self._analyze_error_detail(text)
        return [e["error"] for e in info.get("errors", [])]

    # ──────────────────────────────────────────────────
    #  메인 수집 실행
    # ──────────────────────────────────────────────────
    async def collect(self, session: aiohttp.ClientSession):
        """전체 커뮤니티 소스 수집 실행"""
        for source in self.sources:
            parser_type = source.get("parser", "generic")

            if parser_type == "stackoverflow":
                await self._collect_stackoverflow(session, source)
            elif parser_type == "baeldung":
                await self._collect_baeldung(session, source)
            elif parser_type == "spring-guides":
                await self._collect_spring_guides(session, source)
            elif parser_type == "okky":
                await self._collect_okky(session, source)
            elif parser_type == "github-issues":
                await self._collect_github_issues(session, source)
            else:
                await self._collect_generic(session, source)

            self._save_progress()
