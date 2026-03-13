"""규칙 기반 코드 검증기 — AI 수정 결과를 즉시 검수

턴제 워크플로우에서 AI가 파일별 수정 코드를 생성한 직후,
원본 코드와 대조하여 AI가 자주 범하는 실수를 잡아냅니다.
LLM 호출 없이 파이썬 코드로 0.1초 이내 완료.
"""
import re
import logging

logger = logging.getLogger("nori-server")


class ValidationResult:
    """검증 결과"""
    __slots__ = ("file_path", "warnings", "errors", "passed")

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.warnings: list[str] = []  # ⚠️ 경고 (의심)
        self.errors: list[str] = []    # ❌ 오류 (확실한 문제)
        self.passed = True

    def warn(self, msg: str):
        self.warnings.append(msg)

    def error(self, msg: str):
        self.errors.append(msg)
        self.passed = False

    def to_markdown(self) -> str:
        """검증 결과를 마크다운 문자열로 반환"""
        if not self.warnings and not self.errors:
            return ""

        fname = self.file_path.rsplit("/", 1)[-1] if "/" in self.file_path else self.file_path
        parts = [f"\n> **🔍 검수 결과** `{fname}`"]

        for e in self.errors:
            parts.append(f"> ❌ {e}")
        for w in self.warnings:
            parts.append(f"> ⚠️ {w}")

        if not self.errors:
            parts.append("> ✅ 심각한 문제 없음")

        return "\n".join(parts) + "\n"


def validate_ai_output(
    file_path: str,
    original_code: str,
    ai_output: str,
) -> ValidationResult:
    """AI 수정 결과를 원본 코드와 대조하여 검증

    Args:
        file_path: 파일 경로
        original_code: 원본 소스 코드
        ai_output: AI가 생성한 전체 응답 (마크다운 포함)
    """
    result = ValidationResult(file_path)
    fname = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    lower_fname = fname.lower()

    # "변경없음" 응답이면 검증 스킵 (공백 변형 포함)
    _head = ai_output[:250]
    if "변경없음" in _head or "변경 없음" in _head or "변경사항없음" in _head:
        return result

    # AI 응답에서 코드 블록 추출 (Line XX~YY 형식, 공백/줄바꿈 유연)
    code_blocks = re.findall(r'```[\w]*\s*(?:\(Line[^)]*\))?\s*(.*?)```', ai_output, re.DOTALL)
    if not code_blocks:
        # 코드 블록이 없는데 수정이라면 경고
        if len(ai_output.strip()) > 50:
            result.warn("코드 블록(```)이 없습니다 — 코드 수정이 누락되었을 수 있습니다")
        return result

    merged_code = "\n".join(code_blocks)

    # LLM 출력의 라인 번호 접두사 제거 (97| , 133|  등)
    merged_code = re.sub(r'^\s*\d+\|\s*', '', merged_code, flags=re.MULTILINE)

    # 파일 타입별 검증 분기
    if lower_fname.endswith(".java"):
        _validate_java(result, original_code, merged_code, ai_output, fname)
    elif lower_fname.endswith(".xml"):
        _validate_xml(result, original_code, merged_code, ai_output, fname)
    elif lower_fname.endswith(".jsp"):
        _validate_jsp(result, original_code, merged_code, ai_output, fname)

    # 공통 검증
    _validate_common(result, original_code, merged_code, ai_output, fname)

    return result


def _validate_java(
    result: ValidationResult,
    original: str,
    ai_code: str,
    ai_output: str,
    fname: str,
):
    """Java 파일 검증"""

    # 1. package 선언 보존 확인
    orig_pkg = re.search(r'^package\s+([\w.]+)\s*;', original, re.MULTILINE)
    if orig_pkg:
        ai_pkgs = re.findall(r'^package\s+([\w.]+)\s*;', ai_code, re.MULTILINE)
        if ai_pkgs:
            for ap in ai_pkgs:
                if ap != orig_pkg.group(1):
                    result.error(f"package 변경됨: {orig_pkg.group(1)} → {ap}")

    # 2. 클래스명 보존 확인
    orig_class = re.search(r'(?:public\s+)?class\s+(\w+)', original)
    if orig_class:
        ai_classes = re.findall(r'(?:public\s+)?class\s+(\w+)', ai_code)
        for ac in ai_classes:
            expected = fname.replace(".java", "")
            if ac != orig_class.group(1) and ac != expected:
                result.error(f"클래스명 변경됨: {orig_class.group(1)} → {ac}")

    # 3. @RequestMapping URL 보존 (Controller) — 다른 파일 코드 출력 감지
    if "controller" in fname.lower():
        orig_urls = [u.strip().lower() for u in re.findall(r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)', original)]
        if orig_urls:
            ai_urls = re.findall(r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)', ai_code)
            # AI의 첫 번째(클래스 레벨) RequestMapping이 원본에 없으면 → 다른 컨트롤러 코드
            if ai_urls:
                ai_primary = ai_urls[0].strip().lower()
                if ai_primary not in orig_urls:
                    result.error(
                        f"다른 파일 코드 출력: 원본 @RequestMapping {orig_urls[0]}인데 AI가 '{ai_urls[0]}' 출력 — "
                        f"[수정 대상] {fname}의 실제 경로를 유지하세요"
                    )

    # 4. 중괄호 매칭 (코드 블록이 전체 소스인 경우만)
    if "package " in ai_code:
        open_count = ai_code.count("{")
        close_count = ai_code.count("}")
        if open_count != close_count:
            result.error(f"중괄호 불일치: {{ {open_count}개, }} {close_count}개")

    # 5. 환각 SQL ID 감지 — 원본에 없는 SQL 호출 시 (insertFaqMail 등)
    _orig_sql_refs = set(re.findall(r'["\']([\w.]+\.[\w]+)["\']', original))
    _ai_sql_refs = re.findall(r'(?:insert|update|select|delete)\s*\(\s*["\']([\w.]+\.[\w]+)["\']', ai_code, re.I)
    for sql_ref in _ai_sql_refs:
        if sql_ref not in _orig_sql_refs:
            result.error(f"환각: SQL ID '{sql_ref}'은 원본에 없습니다. 기존 SQL만 사용하세요.")
            break

    # 6. import 사용 확인 (전체 소스가 있는 경우)
    if "import " in ai_code and "package " in ai_code:
        imports = re.findall(r'import\s+[\w.]+\.(\w+)\s*;', ai_code)
        imported_classes = set(imports)
        # 흔한 미사용 import는 무시 (경고만)
        body = ai_code.split("class ", 1)[-1] if "class " in ai_code else ai_code
        unused = [imp for imp in imported_classes
                  if imp not in body and imp not in ("*",)]
        if len(unused) > 3:
            result.warn(f"미사용 import {len(unused)}개")


def _validate_xml(
    result: ValidationResult,
    original: str,
    ai_code: str,
    ai_output: str,
    fname: str,
):
    """XML/MyBatis 파일 검증"""

    # 1. MyBatis namespace 보존
    orig_ns = re.search(r'namespace\s*=\s*["\']([^"\']+)', original)
    if orig_ns:
        ai_ns = re.findall(r'namespace\s*=\s*["\']([^"\']+)', ai_code)
        for ns in ai_ns:
            if ns != orig_ns.group(1):
                result.error(f"MyBatis namespace 변경됨: {orig_ns.group(1)} → {ns}")

    # 2. 기존 SQL ID 삭제 확인 (전체 소스인 경우)
    if "<mapper" in ai_code and "</mapper>" in ai_code:
        orig_ids = set(re.findall(r'<(?:select|insert|update|delete)\s+id\s*=\s*["\'](\w+)', original))
        ai_ids = set(re.findall(r'<(?:select|insert|update|delete)\s+id\s*=\s*["\'](\w+)', ai_code))
        deleted = orig_ids - ai_ids
        if deleted:
            result.error(f"기존 SQL ID 삭제됨: {', '.join(sorted(deleted))}")

    # 3. XML 태그 닫힘 확인
    if "<mapper" in ai_code:
        open_tags = len(re.findall(r'<(select|insert|update|delete)\s', ai_code))
        close_tags = len(re.findall(r'</(select|insert|update|delete)>', ai_code))
        if open_tags != close_tags:
            result.error(f"SQL 태그 미닫힘: 열기 {open_tags}개, 닫기 {close_tags}개")


def _validate_jsp(
    result: ValidationResult,
    original: str,
    ai_code: str,
    ai_output: str,
    fname: str,
):
    """JSP 파일 검증"""

    # 1. form action URL 보존
    orig_actions = re.findall(r'action\s*=\s*["\']([^"\']+)', original)
    if orig_actions:
        ai_actions = re.findall(r'action\s*=\s*["\']([^"\']+)', ai_code)
        for aa in ai_actions:
            if aa not in orig_actions and "<%=" not in aa:
                for oa in orig_actions:
                    if _similar_url(oa, aa):
                        result.warn(f"form action 변경 의심: {oa} → {aa}")

    # 2. include 경로 보존
    orig_includes = set(re.findall(r'<%@\s*include\s+file\s*=\s*["\']([^"\']+)', original))
    if orig_includes:
        ai_includes = set(re.findall(r'<%@\s*include\s+file\s*=\s*["\']([^"\']+)', ai_code))
        deleted = orig_includes - ai_includes
        if deleted and "<%" in ai_code:
            result.warn(f"include 삭제 의심: {', '.join(deleted)}")


def _validate_common(
    result: ValidationResult,
    original: str,
    ai_code: str,
    ai_output: str,
    fname: str,
):
    """공통 검증 (모든 파일 타입)"""

    # 1. ★ 마킹 확인 (실제 코드 수정이 상당한 경우에만 요구)
    has_code_change = len(ai_code.strip()) > 100
    has_star = "★" in ai_output
    if has_code_change and not has_star:
        result.warn("★ 수정 마킹이 없습니다")

    # 2. 다른 파일 코드 섞임 감지 (전체 소스/패키지 있을 때만 — 스니펫/참고 블록 제외)
    if fname.endswith(".java"):
        expected_class = fname.replace(".java", "")
        # package 있거나 class 선언이 현재 파일인 경우에만 검사 (참고용 스니펫 오탐 방지)
        if "package " in ai_code or f"class {expected_class}" in ai_code:
            other_classes = re.findall(r'(?:public\s+)?class\s+(\w+)', ai_code)
            for oc in other_classes:
                if oc != expected_class and not oc[0].islower():
                    if "inner" not in ai_output.lower() and "static class" not in ai_code:
                        result.warn(f"다른 클래스 코드 의심: {oc} (현재 파일: {expected_class})")

    # 3. 모순 감지: "변경없음" + 코드 블록
    if "변경없음" in ai_output and len(ai_code.strip()) > 50:
        result.warn("'변경없음'이라면서 코드 블록이 있습니다")


def _similar_url(url_a: str, url_b: str) -> bool:
    """두 URL이 유사한지 (같은 리소스의 변형인지)"""
    a = url_a.rstrip("/").lower()
    b = url_b.rstrip("/").lower()
    if a == b:
        return True
    # 경로의 마지막 부분이 같으면 유사
    a_last = a.rsplit("/", 1)[-1]
    b_last = b.rsplit("/", 1)[-1]
    return a_last == b_last and a_last != ""
