"""
MariaDB 연결 풀 관리 — Structure Index Database
aiomysql 기반 비동기 커넥션 풀
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aiomysql

from app.config.settings import Settings

logger = logging.getLogger("nori-server")

_pool: aiomysql.Pool | None = None


async def init_db_pool(settings: Settings) -> None:
    """서버 시작 시 커넥션 풀 초기화"""
    global _pool
    try:
        _pool = await aiomysql.create_pool(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            db=settings.db_name,
            charset="utf8mb4",
            minsize=1,
            maxsize=settings.db_pool_size,
            autocommit=True,
        )
        logger.info(
            f"MariaDB 연결 풀 초기화 완료 ({settings.db_host}:{settings.db_port}/{settings.db_name})"
        )
    except Exception as e:
        logger.error(f"MariaDB 연결 풀 초기화 실패: {e}")
        _pool = None


async def close_db_pool() -> None:
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("MariaDB 연결 풀 종료")


def get_pool() -> aiomysql.Pool | None:
    return _pool


@asynccontextmanager
async def get_conn() -> AsyncGenerator[aiomysql.Connection, None]:
    """단건 쿼리용 커넥션 컨텍스트 매니저"""
    pool = get_pool()
    if pool is None:
        raise RuntimeError("DB 풀이 초기화되지 않았습니다")
    async with pool.acquire() as conn:
        yield conn
