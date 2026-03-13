"""
Nori AI — 임베딩 파이프라인 CLI

수집된 JSON 데이터를 파싱 → 청킹 → ChromaDB 적재까지 일괄 처리.

사용법:
  python -m nori_embedding.main --target all
  python -m nori_embedding.main --target javadoc
  python -m nori_embedding.main --target spring
  python -m nori_embedding.main --target web-ui desktop-ui
  python -m nori_embedding.main --stats
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# 패키지 루트를 sys.path에 추가
_EMBED_ROOT = Path(__file__).resolve().parent
if str(_EMBED_ROOT) not in sys.path:
    sys.path.insert(0, str(_EMBED_ROOT))

from parsers.json_parser import parse_directory
from parsers.smart_chunker import chunk_documents, ChunkConfig
from loaders.vector_loader import VectorLoader

# ── 로깅 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nori-embedding")

# ── 기본 경로 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "nori-collector" / "data"
CHROMA_DIR = PROJECT_ROOT / "nori-embedding" / "db" / "chroma"

# ── 소스 타입 → 데이터 디렉토리 ──
TARGET_MAP = {
    "javadoc":    ("javadoc",    ["javadoc"]),
    "spring":     ("spring-docs", ["spring-doc"]),
    "web-ui":     ("web-ui",     ["web-ui"]),
    "desktop-ui": ("desktop-ui", ["desktop-ui"]),
    "egov":       ("egov",       ["egov"]),
    "community":  ("community",  ["community"]),
    "database":   ("database",   ["database", "mariadb-doc", "mongodb-doc",
                                   "oracle-doc", "postgresql-doc", "redis-doc",
                                   "sqlite-doc"]),
}
ALL_TARGETS = list(TARGET_MAP.keys())


def run_pipeline(targets: list[str], data_dir: Path, chroma_dir: Path,
                 max_tokens: int = 512, overlap: int = 50):
    """타겟별 임베딩 파이프라인 실행"""
    start = time.time()
    config = ChunkConfig(max_tokens=max_tokens, overlap_tokens=overlap)
    loader = VectorLoader(chroma_dir)
    loader.connect()

    total_docs = 0
    total_chunks = 0

    for target in targets:
        entry = TARGET_MAP.get(target)
        if not entry:
            logger.warning(f"알 수 없는 타겟: {target} (사용 가능: {ALL_TARGETS})")
            continue

        sub_dir, source_types = entry
        target_path = data_dir / sub_dir

        if not target_path.is_dir():
            logger.warning(f"데이터 디렉토리 없음: {target_path}")
            continue

        logger.info(f"═══ [{target}] 파이프라인 시작 ═══")

        # Step 1: 파싱
        logger.info(f"[{target}] Step 1/3: JSON 파싱 중...")
        docs = parse_directory(target_path, source_types=source_types)
        if not docs:
            logger.warning(f"[{target}] 파싱 결과 0건, 스킵")
            continue
        total_docs += len(docs)

        # Step 2: 청킹
        logger.info(f"[{target}] Step 2/3: 텍스트 청킹 중... ({len(docs)}개 원문)")
        chunks = chunk_documents(docs, config)
        total_chunks += len(chunks)

        # Step 3: ChromaDB 적재
        logger.info(f"[{target}] Step 3/3: ChromaDB 적재 중... ({len(chunks)}개 청크)")
        stats = loader.load(chunks)
        logger.info(f"[{target}] 완료! 적재: {stats['total']}개")

    elapsed = time.time() - start

    # 최종 통계
    logger.info("═══ 전체 결과 ═══")
    logger.info(f"  파싱 문서: {total_docs}개")
    logger.info(f"  생성 청크: {total_chunks}개")
    logger.info(f"  소요 시간: {elapsed:.1f}초")

    db_stats = loader.get_stats()
    logger.info("═══ ChromaDB 현황 ═══")
    for name, count in db_stats.items():
        logger.info(f"  {name}: {count}개")
    logger.info(f"  합계: {sum(db_stats.values())}개")


def show_stats(chroma_dir: Path):
    """ChromaDB 통계 출력"""
    loader = VectorLoader(chroma_dir)
    loader.connect()
    stats = loader.get_stats()

    print("\n📊 ChromaDB 벡터 DB 현황")
    print("─" * 40)
    total = 0
    for name, count in sorted(stats.items()):
        bar = "▓" * min(count // 100, 30) if count > 0 else "░"
        print(f"  {name:15s} │ {count:>7,}개 {bar}")
        total += count
    print("─" * 40)
    print(f"  {'합계':15s} │ {total:>7,}개")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Nori AI 임베딩 파이프라인 — 수집 데이터를 벡터 DB에 적재",
    )
    parser.add_argument(
        "--target", nargs="+", default=None,
        help=f"처리 대상 (선택: {', '.join(ALL_TARGETS)}, all)",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="ChromaDB 현재 통계 출력",
    )
    parser.add_argument(
        "--data-dir", type=str, default=str(DATA_DIR),
        help="수집 데이터 디렉토리 경로",
    )
    parser.add_argument(
        "--chroma-dir", type=str, default=str(CHROMA_DIR),
        help="ChromaDB 저장 디렉토리",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=512,
        help="청크 최대 토큰 수 (기본: 512)",
    )
    parser.add_argument(
        "--overlap", type=int, default=50,
        help="청크 오버랩 토큰 수 (기본: 50)",
    )

    args = parser.parse_args()

    if args.stats:
        show_stats(Path(args.chroma_dir))
        return

    if not args.target:
        parser.print_help()
        sys.exit(1)

    targets = ALL_TARGETS if "all" in args.target else args.target
    run_pipeline(
        targets=targets,
        data_dir=Path(args.data_dir),
        chroma_dir=Path(args.chroma_dir),
        max_tokens=args.max_tokens,
        overlap=args.overlap,
    )


if __name__ == "__main__":
    main()
