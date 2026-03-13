#!/usr/bin/env python3
"""FAQ 질문 시 파일 선별 검증 — 실제 프로젝트 프로필과 대조

사용법:
  python scripts/verify_faq_selection.py
  python scripts/verify_faq_selection.py "경로/프로젝트/.nori-profile.md"
  python scripts/verify_faq_selection.py "경로/프로젝트"   # 프로젝트 루트에서 .nori-profile.md 탐색

Eclipse 프로젝트 루트에 .nori-profile.md가 있으면 해당 경로를 인자로 주세요.
"""
import sys
from pathlib import Path

# nori-server 모듈
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.api.search_chat import (
    _fix_file_paths,
    _expand_related_files,
    _extract_profile_paths,
    _keyword_search_paths,
)


def find_profile(path_arg: str = None) -> tuple[Path | None, str]:
    """프로필 파일 찾기. (경로, 내용) 반환"""
    if path_arg:
        p = Path(path_arg)
        if p.is_file():
            return p, p.read_text(encoding="utf-8")
        if p.is_dir():
            pf = p / ".nori-profile.md"
            if pf.exists():
                return pf, pf.read_text(encoding="utf-8")
    # 기본 탐색: nori-server/data, 현재 디렉
    for base in [Path(__file__).parent.parent / "data", Path.cwd()]:
        for sub in [base, base / "memos"]:
            if sub.exists():
                for f in sub.rglob(".nori-profile.md"):
                    return f, f.read_text(encoding="utf-8")
    return None, ""


def main():
    path_arg = sys.argv[1] if len(sys.argv) > 1 else None
    profile_path, profile_content = find_profile(path_arg)

    if not profile_content or len(profile_content) < 200:
        print("❌ 프로필을 찾을 수 없습니다.")
        print("   사용법: python verify_faq_selection.py <프로젝트경로 또는 .nori-profile.md 경로>")
        print("   예: python verify_faq_selection.py C:\\workspace\\gpoint")
        sys.exit(1)

    print(f"📂 프로필: {profile_path}")
    print()

    # 프로필에서 실제 경로 추출
    profile_paths = _extract_profile_paths(profile_content)
    print(f"📋 프로필 내 경로 수: {len(profile_paths)}개")
    print()

    # FAQ 관련 파일 (이름 기준) — 프로젝트에 실제 있어야 하는 파일
    question = "faq 게시판에 메일 항목을 추가로 받을 수 있게 해줘"
    faq_expected = []  # faq, cooper, inq, mail 포함
    faq_should_exclude = []  # approval, event, board(단독) 등

    for fp in profile_paths:
        fn = fp.rsplit("/", 1)[-1].lower() if "/" in fp else fp.lower()
        if any(d in fn for d in ("faq", "cooper", "inq", "mail", "email")):
            faq_expected.append(fp)
        elif any(d in fn for d in ("approval", "event")) and not any(d in fn for d in ("faq", "cooper", "inq")):
            faq_should_exclude.append(fp)
        elif "board" in fn and "faq" not in fn and "cooper" not in fn and "inq" not in fn:
            # BoardController, BoardService 등 (FAQ 무관)
            faq_should_exclude.append(fp)

    print("=== 프로젝트 내 FAQ 관련 파일 (이름 기준) ===")
    for p in faq_expected[:20]:
        print(f"  ✅ {p.rsplit('/',1)[-1]}")
    print()
    print("=== FAQ 질문 시 제외되어야 할 파일 ===")
    for p in faq_should_exclude[:15]:
        print(f"  ⛔ {p.rsplit('/',1)[-1]}")
    print()

    # 볼트 선택 시뮬레이션
    llm_files_bad = ["FaqController.java", "faqList.jsp", "Service, mybatis_sql...board.xml 등 프로필에 실제 있는 경로"]
    llm_files_good = ["FaqController.java", "faqList.jsp", "CooperInqController.java", "BoardMailVO.java"]

    print("=== 시나리오 1: LLM이 쓰레기 출력한 경우 ===")
    fixed_bad = _fix_file_paths(llm_files_bad, profile_content, question)
    expanded_bad = _expand_related_files(fixed_bad, profile_content, question)
    names_bad = [f.rsplit("/", 1)[-1] for f in expanded_bad]
    print(f"  선택된 파일: {names_bad}")
    exclude_fnames = [p.rsplit("/",1)[-1].lower() for p in faq_should_exclude]
    excluded_in_bad = [n for n in names_bad if n.lower() in exclude_fnames]
    if excluded_in_bad:
        print(f"  ⚠️ 제외되었어야 할데 포함됨: {excluded_in_bad}")
    else:
        print("  ✅ Approval/Event/Board 일반 제외됨")
    print()

    print("=== 시나리오 2: LLM이 올바르게 출력한 경우 ===")
    fixed_good = _fix_file_paths(llm_files_good, profile_content, question)
    expanded_good = _expand_related_files(fixed_good, profile_content, question)
    names_good = [f.rsplit("/", 1)[-1] for f in expanded_good]
    print(f"  선택된 파일: {names_good}")
    missing = [p for p in faq_expected if p.rsplit("/",1)[-1] not in names_good]
    if missing and len(missing) <= 5:
        print(f"  ⚠️ 누락 가능: {[p.rsplit('/',1)[-1] for p in missing[:5]]}")
    print()

    print("=== 키워드 검색(폴백) 결과 ===")
    kw_result = _keyword_search_paths(question, profile_content, max_results=10)
    kw_names = [f.rsplit("/", 1)[-1] for f in kw_result]
    print(f"  {kw_names}")
    kw_excluded = [n for n in kw_names if n.lower() in exclude_fnames]
    if kw_excluded:
        print(f"  ⚠️ 제외되었어야 할데 포함됨: {kw_excluded}")
    else:
        print("  ✅ 도메인 필터 적용됨")
    print()

    print("=== 요약 ===")
    ok1 = not excluded_in_bad
    ok2 = len(names_good) >= 2
    ok3 = not kw_excluded
    if ok1 and ok2 and ok3:
        print("✅ 볼트 파일 선별 로직이 프로젝트와 일치합니다.")
    else:
        print("⚠️ 일부 불일치가 있습니다. 위 상세를 확인하세요.")

if __name__ == "__main__":
    main()
