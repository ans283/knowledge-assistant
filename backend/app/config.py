from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py → app → backend → knowledge-assistant
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- identity ---
    app_name: str = "knowledge-assistant"

    # --- paths (absolute, derived from this file's location) ---
    data_dir: Path = PROJECT_ROOT / "data"
    chroma_dir: Path = PROJECT_ROOT / "data" / "chroma"
    normalized_dir: Path = PROJECT_ROOT / "data" / "normalized"
    corpus_dir: Path = PROJECT_ROOT / "data" / "corpus"
    registry_db: Path = PROJECT_ROOT / "data" / "registry.db"

    # --- vector store ---
    collection_name: str = "policy_chunks"

    # --- embeddings (used from Step 5) ---
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_dim: int = 768

    # --- retrieval defaults (used from Step 7) ---
    retrieve_candidates: int = 20   # before rerank
    retrieve_top_k: int = 5         # after rerank
    rrf_k: int = 60

    # --- generation (used from Step 8) ---
    llm_provider: str = "mock"      # mock | anthropic | gemini
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None

    # --- abstention gate (used from Step 9) ---
    min_rerank_score: float = 0.30


settings = Settings()