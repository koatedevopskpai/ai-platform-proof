from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ai:ai@localhost:5432/aiplatform"
    embedding_mode: str = "mock"  # "mock" | "openai"
    embedding_dim: int = 384
    embedding_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    openai_api_key: str = ""
    rag_top_k: int = 4
    rag_min_score: float = 0.10


settings = Settings()