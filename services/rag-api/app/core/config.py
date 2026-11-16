from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ai:ai@localhost:5432/aiplatform"
    embedding_mode: str = "mock"  # "mock" | "openai"
    embedding_dim: int = 384
    embedding_model: str = "text-embedding-3-small"
    # text-embedding-3-* accepts a dimensions parameter. Requesting the schema width keeps
    # vector(384), the HNSW index and the RDS parity receipt valid without a migration.
    # Set false for providers that reject the parameter; the response is width-checked either way.
    embedding_send_dimensions: bool = True
    llm_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    openai_api_key: str = ""
    # Generation may target a different OpenAI-compatible provider than embeddings. Empty values
    # fall back to the OPENAI_* settings, so single-provider deployments need no extra config.
    llm_base_url: str = ""
    llm_api_key: str = ""
    rag_top_k: int = 4
    rag_min_score: float = 0.10


settings = Settings()