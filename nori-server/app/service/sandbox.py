"""
Execution Sandbox — 생성 코드 안전 실행 환경

Phase 10 구현체.
현재는 Safety Guard 검사 + 구조 검증만 수행하며,
Docker 컨테이너 실행은 환경이 갖춰진 경우에만 활성화된다.

실행 흐름:
  코드 입력 → Safety Guard → (Docker 가용 시) 컨테이너 빌드/실행 → 결과 반환
  Docker 없음 → Safety Guard 통과 여부 + 정적 분석 결과만 반환
"""
import logging
import re
import shutil
from dataclasses import dataclass, field

from app.service.safety_guard import check_code_safety, GuardResult

logger = logging.getLogger("nori-server")


@dataclass
class SandboxResult:
    """Sandbox 실행 결과"""
    passed_safety: bool
    violations: list[dict] = field(default_factory=list)
    static_warnings: list[str] = field(default_factory=list)
    sandbox_mode: str = "static"   # static | docker
    build_output: str = ""
    test_output: str = ""
    success: bool = False

    def summary(self) -> str:
        if not self.passed_safety:
            return f"[BLOCKED] Safety Guard 위반: {len(self.violations)}건"
        if self.static_warnings:
            return f"[WARNING] 정적 분석 경고 {len(self.static_warnings)}건: " + "; ".join(self.static_warnings[:3])
        return "[OK] 안전 검사 통과"


# ── 정적 분석 경고 패턴 ──
_STATIC_WARN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("하드코딩 비밀번호",
     re.compile(r'(?:password|passwd|secret|api_key)\s*=\s*["\'][^"\']{4,}["\']', re.I)),
    ("TODO/FIXME",
     re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.I)),
    ("System.out.println 사용",
     re.compile(r"\bSystem\.out\.println\s*\(")),
    ("printStackTrace 사용",
     re.compile(r"\.printStackTrace\s*\(")),
    ("catch (Exception e) 빈 블록",
     re.compile(r"catch\s*\(\s*Exception\s+\w+\s*\)\s*\{\s*\}")),
]


def _static_analyze(code: str) -> list[str]:
    """정적 패턴 분석 — 경고 메시지 목록 반환 (차단 아님)."""
    warnings: list[str] = []
    for label, pattern in _STATIC_WARN_PATTERNS:
        if pattern.search(code):
            warnings.append(label)
    return warnings


def _is_docker_available() -> bool:
    """Docker CLI 가용 여부 확인."""
    return shutil.which("docker") is not None


async def run_in_sandbox(code: str, language: str = "java") -> SandboxResult:
    """코드를 샌드박스에서 검증.

    현재 구현:
      1. Safety Guard 검사 (차단 패턴)
      2. 정적 분석 경고 수집
      3. Docker 가용 시 → 컨테이너 실행 (미구현 — 확장 포인트)

    Args:
        code: 실행할 코드 문자열
        language: 언어 힌트 ("java", "python", "sql")

    Returns:
        SandboxResult
    """
    # 1) Safety Guard
    guard: GuardResult = check_code_safety(code)
    if not guard.safe:
        logger.warning("[Sandbox] Safety Guard 차단: %s", guard.summary())
        return SandboxResult(
            passed_safety=False,
            violations=guard.violations,
            sandbox_mode="static",
            success=False,
        )

    # 2) 정적 분석
    warnings = _static_analyze(code)
    if warnings:
        logger.info("[Sandbox] 정적 분석 경고: %s", warnings)

    # 3) Docker 실행 (가용 시 확장 포인트)
    if _is_docker_available():
        logger.info("[Sandbox] Docker 감지됨 — 컨테이너 실행 (미구현 확장 포인트)")
        # TODO: docker run --rm -i --network=none openjdk:17 ... 형태로 확장
        sandbox_mode = "docker_stub"
    else:
        sandbox_mode = "static"

    return SandboxResult(
        passed_safety=True,
        violations=[],
        static_warnings=warnings,
        sandbox_mode=sandbox_mode,
        success=True,
    )
