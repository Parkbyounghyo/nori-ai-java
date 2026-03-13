"""
Java SE 17 API 문서(JavaDoc) 수집기
Oracle 공식 JavaDoc에서 핵심 패키지의 클래스별 문서를 수집
"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from .base import BaseCollector

logger = logging.getLogger("nori-collector")


class JavaDocCollector(BaseCollector):
    """Java SE 17 JavaDoc 수집기"""

    def __init__(self, base_url: str, packages: list[str], output_dir: str, **kwargs):
        super().__init__(output_dir=output_dir, **kwargs)
        self.base_url = base_url.rstrip("/")
        self.packages = packages

    def _package_to_path(self, package: str) -> str:
        """패키지명 → URL 경로 변환 (java.util → java.base/java/util)"""
        # Java 17 모듈 매핑
        module_map = {
            "java.lang": "java.base",
            "java.util": "java.base",
            "java.io": "java.base",
            "java.nio": "java.base",
            "java.net": "java.base",
            "java.time": "java.base",
            "java.math": "java.base",
            "java.text": "java.base",
            "java.sql": "java.sql",
        }
        # 가장 구체적인 매칭 찾기
        module = "java.base"
        for pkg_prefix, mod in module_map.items():
            if package.startswith(pkg_prefix):
                module = mod

        pkg_path = package.replace(".", "/")
        return f"{module}/{pkg_path}"

    async def _get_class_list(self, session: aiohttp.ClientSession,
                               package: str) -> list[dict]:
        """패키지의 클래스 목록 가져오기"""
        pkg_path = self._package_to_path(package)
        url = f"{self.base_url}/{pkg_path}/package-summary.html"
        html = await self.fetch(session, url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        classes = []

        # 클래스/인터페이스/열거형 테이블에서 추출
        for table in soup.select("div.summary-table"):
            for row in table.select("div.col-first"):
                link = row.find("a")
                if link and link.get("href"):
                    href = link["href"]
                    # 상대 경로 → 절대 경로
                    if not href.startswith("http"):
                        class_url = f"{self.base_url}/{pkg_path}/{href}"
                    else:
                        class_url = href
                    class_name = link.get_text(strip=True)
                    classes.append({
                        "name": class_name,
                        "url": class_url,
                        "package": package
                    })

        # 대체 구조 (오라클 JavaDoc 버전에 따라 구조 다를 수 있음)
        if not classes:
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                text = link.get_text(strip=True)
                if href.endswith(".html") and not href.startswith("http") and text:
                    if href not in ("package-summary.html", "package-tree.html",
                                     "package-use.html"):
                        class_url = f"{self.base_url}/{pkg_path}/{href}"
                        classes.append({
                            "name": text,
                            "url": class_url,
                            "package": package
                        })

        logger.info(f"  {package}: {len(classes)}개 클래스 발견")
        return classes

    def _parse_class_doc(self, html: str, class_info: dict) -> dict:
        """클래스 HTML → 구조화 데이터 파싱"""
        soup = BeautifulSoup(html, "lxml")

        # 클래스 설명
        description = ""
        desc_block = soup.select_one("div.class-description div.block")
        if not desc_block:
            desc_block = soup.select_one("div.description div.block")
        if desc_block:
            description = desc_block.get_text(separator="\n", strip=True)

        # 클래스 시그니처
        signature = ""
        sig_elem = soup.select_one("div.type-signature")
        if sig_elem:
            signature = sig_elem.get_text(separator=" ", strip=True)

        # 메서드 목록 파싱
        methods = []
        for method_detail in soup.select("section.method-details > ul > li"):
            method_name_elem = method_detail.select_one("h3")
            method_sig_elem = method_detail.select_one("div.member-signature")
            method_desc_elem = method_detail.select_one("div.block")

            if method_name_elem:
                methods.append({
                    "name": method_name_elem.get_text(strip=True),
                    "signature": method_sig_elem.get_text(separator=" ", strip=True) if method_sig_elem else "",
                    "description": method_desc_elem.get_text(separator="\n", strip=True) if method_desc_elem else "",
                })

        # 메서드 요약 테이블 (상세가 없을 때 대체)
        if not methods:
            for row in soup.select("div.method-summary div.summary-table div.col-first"):
                link = row.find("a")
                if link:
                    sibling_desc = row.find_next_sibling("div", class_="col-last")
                    methods.append({
                        "name": link.get_text(strip=True),
                        "signature": "",
                        "description": sibling_desc.get_text(strip=True) if sibling_desc else "",
                    })

        # 생성자 파싱
        constructors = []
        for ctor in soup.select("section.constructor-details > ul > li"):
            ctor_sig = ctor.select_one("div.member-signature")
            ctor_desc = ctor.select_one("div.block")
            if ctor_sig:
                constructors.append({
                    "signature": ctor_sig.get_text(separator=" ", strip=True),
                    "description": ctor_desc.get_text(separator="\n", strip=True) if ctor_desc else "",
                })

        return {
            "source_type": "javadoc",
            "class_name": class_info["name"],
            "package_name": class_info["package"],
            "url": class_info["url"],
            "signature": signature,
            "description": description,
            "constructors": constructors,
            "methods": methods,
            "method_count": len(methods),
        }

    async def collect(self, session: aiohttp.ClientSession):
        """전체 수집 실행"""
        for package in self.packages:
            logger.info(f"패키지 수집 시작: {package}")

            # 1. 클래스 목록 가져오기
            classes = await self._get_class_list(session, package)
            if not classes:
                logger.warning(f"  클래스 목록을 가져올 수 없음: {package}")
                continue

            # 2. 각 클래스 문서 수집
            for cls in classes:
                if cls["url"] in self._collected_urls:
                    continue

                html = await self.fetch(session, cls["url"])
                if html:
                    parsed = self._parse_class_doc(html, cls)
                    # 파싱 데이터 저장
                    safe_name = f"{cls['package']}.{cls['name']}.json"
                    await self.save_parsed(safe_name, parsed, subfolder=cls["package"])
                    self.mark_collected(cls["url"])
                    logger.info(f"  ✓ {cls['package']}.{cls['name']} (메서드 {parsed['method_count']}개)")

                await asyncio.sleep(self.delay)

            logger.info(f"패키지 수집 완료: {package}")
