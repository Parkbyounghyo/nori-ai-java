"""
Safety Guard Layer — LLM 생성 코드·명령의 위험 패턴 차단

차단 대상:
  - 파일 삭제 (rm, del, shutil.rmtree 등)
  - OS 명령 실행 (os.system, subprocess, Runtime.exec 등)
  - DB 구조 변경/삭제 (DROP, TRUNCATE, DELETE 무조건)
  - 권한 변경 (chmod, GRANT, REVOKE)
  - 외부 네트워크 직접 호출 (curl, wget, requests.get 외부 URL)

허용:
  - 파일 읽기 / 쓰기 (안전한 범위)
  - 코드 수정 제안
  - 쿼리 (SELECT, INSERT, UPDATE — WHERE 조건 포함)
"""
import re
from dataclasses import dataclass, field

# ── 차단 패턴 정의 ──
@dataclass
class BlockRule:
    name: str
    pattern: re.Pattern
    severity: str = "high"   # high / medium
    message: str = ""


_BLOCK_RULES: list[BlockRule] = [
    # OS 명령 실행
    BlockRule("os_command_exec",
              re.compile(r"\bos\.system\s*\(|\bsubprocess\s*\.\s*(run|call|Popen|check_output)"
                         r"|\bRuntime\.getRuntime\(\)\.exec\b"
                         r"|\bProcessBuilder\b.*\bstart\b",
                         re.I),
              message="OS 명령 실행은 허용되지 않습니다."),
    # 파일 삭제
    BlockRule("file_delete",
              re.compile(r"\brm\s+-rf\b|\bshutil\.rmtree\b|\bos\.remove\s*\("
                         r"|\bFiles\.delete\b|\bFile\s*\.\s*delete\s*\(\)"
                         r"|\bdel\s+/[sqa]\b",
                         re.I),
              message="파일 삭제 명령은 허용되지 않습니다."),
    # DB 구조 파괴
    BlockRule("db_destructive",
              re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX)\b"
                         r"|\bTRUNCATE\s+TABLE\b"
                         r"|\bDELETE\s+FROM\s+\w+\s*(?:;|$)",  # WHERE 없는 DELETE
                         re.I | re.M),
              message="DB 구조 파괴 또는 무조건 DELETE는 허용되지 않습니다."),
    # 권한 변경
    BlockRule("permission_change",
              re.compile(r"\bchmod\s+[0-9]+\b|\bchown\b"
                         r"|\bGRANT\s+ALL\b|\bREVOKE\b"
                         r"|\bsudo\b",
                         re.I),
              message="권한 변경 명령은 허용되지 않습니다."),
    # 외부 네트워크 요청 (http/https 외부 도메인)
    BlockRule("external_network",
              re.compile(r"\bcurl\s+https?://(?!localhost|127\.0\.0\.1)"
                         r"|\bwget\s+https?://(?!localhost|127\.0\.0\.1)"
                         r"|requests\.(get|post|put|delete)\s*\(\s*['\"]https?://(?!localhost|127\.0\.0\.1)",
                         re.I),
              severity="medium",
              message="외부 네트워크 접근 코드가 감지되었습니다."),
    # 메모리 덤프 / 리플렉션 악용
    BlockRule("reflection_abuse",
              re.compile(r"getDeclaredMethod.*setAccessible\s*\(\s*true\s*\)"
                         r"|Field.*setAccessible\s*\(\s*true\s*\).*set\s*\(",
                         re.S),
              severity="medium",
              message="리플렉션을 통한 접근 제어 우회는 허용되지 않습니다."),
]


@dataclass
class GuardResult:
    """Safety Guard 검사 결과"""
    safe: bool
    violations: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        if self.safe:
            return "안전"
        msgs = "; ".join(v["message"] for v in self.violations)
        return f"위험 감지: {msgs}"


def check_code_safety(code: str) -> GuardResult:
    """코드 문자열에서 위험 패턴 스캔.

    Args:
        code: LLM이 생성한 코드 또는 명령 문자열

    Returns:
        GuardResult — safe=False 이면 실행 차단
    """
    violations: list[dict] = []
    for rule in _BLOCK_RULES:
        m = rule.pattern.search(code)
        if m:
            violations.append({
                "rule": rule.name,
                "severity": rule.severity,
                "message": rule.message,
                "matched": m.group(0)[:80],
            })
    return GuardResult(safe=len(violations) == 0, violations=violations)


def check_sql_safety(sql: str) -> GuardResult:
    """SQL 전용 안전 검사 (DB 파괴 패턴 + WHERE 없는 DELETE/UPDATE).

    SELECT/INSERT/UPDATE(WHERE 포함)는 허용.
    DROP/TRUNCATE/DELETE(WHERE 없음)는 차단.
    """
    violations: list[dict] = []

    # DROP / TRUNCATE
    if re.search(r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", sql, re.I):
        violations.append({
            "rule": "sql_drop",
            "severity": "high",
            "message": "DROP 문은 허용되지 않습니다.",
            "matched": "DROP",
        })
    if re.search(r"\bTRUNCATE\b", sql, re.I):
        violations.append({
            "rule": "sql_truncate",
            "severity": "high",
            "message": "TRUNCATE 문은 허용되지 않습니다.",
            "matched": "TRUNCATE",
        })

    # WHERE 없는 DELETE
    delete_m = re.search(r"\bDELETE\s+FROM\s+\w+", sql, re.I)
    if delete_m and not re.search(r"\bWHERE\b", sql, re.I):
        violations.append({
            "rule": "delete_no_where",
            "severity": "high",
            "message": "WHERE 조건 없는 DELETE는 허용되지 않습니다.",
            "matched": delete_m.group(0)[:60],
        })

    # WHERE 없는 UPDATE
    update_m = re.search(r"\bUPDATE\s+\w+\s+SET\b", sql, re.I)
    if update_m and not re.search(r"\bWHERE\b", sql, re.I):
        violations.append({
            "rule": "update_no_where",
            "severity": "medium",
            "message": "WHERE 조건 없는 UPDATE는 주의가 필요합니다.",
            "matched": update_m.group(0)[:60],
        })

    return GuardResult(safe=len(violations) == 0, violations=violations)


def filter_safe_code(code: str, raise_on_violation: bool = False) -> tuple[str, GuardResult]:
    """코드 안전 검사 후 (코드, 결과) 반환.

    Args:
        code: 검사 대상 코드
        raise_on_violation: True이면 위반 시 ValueError 발생

    Returns:
        (code, GuardResult) — 위반이 없으면 그대로 반환
    """
    result = check_code_safety(code)
    if not result.safe and raise_on_violation:
        raise ValueError(f"Safety Guard 위반: {result.summary()}")
    return code, result
