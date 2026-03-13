"""
Nori AI 서버 환경설정
Pydantic Settings 기반 — 환경변수 / .env 파일에서 로드
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NORI_",
        case_sensitive=False,
    )

    # ── AI LLM 설정 ──
    ai_provider: str = "ollama"           # openai | ollama
    ai_api_key: str = ""                 # NORI_AI_API_KEY
    ai_model: str = "gpt-4o-mini"        # 기본 모델
    ai_temperature: float = 0.2          # 코드 생성은 낮은 temperature
    ai_max_tokens: int = 4096

    # ── Ollama (로컬 LLM) ──
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"

    # ── 벡터 DB ──
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    chroma_persist_dir: str = "../nori-embedding/db/chroma"

    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_enabled: bool = False         # 초기엔 ChromaDB만 사용

    # ── 임베딩 ──
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # ── 서버 ──
    server_host: str = "0.0.0.0"
    server_port: int = 8090
    api_key: str = ""                    # 서버 접근 인증 키 (비어있으면 인증 비활성)
    cors_origins: str = "*"
    debug: bool = False

    # ── 경로 ──
    data_dir: str = "../nori-collector/data"
    prompts_dir: str = "prompts"

    @property
    def chroma_persist_path(self) -> Path:
        return Path(self.chroma_persist_dir).resolve()

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    @property
    def prompts_path(self) -> Path:
        return Path(self.prompts_dir).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
