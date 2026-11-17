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

    # Cost guards. Measured before the provider is called, so a breach spends nothing.
    # Completion is capped by sending max_tokens; prompt size is estimated and rejected, never
    # truncated, because a trimmed prompt produces an answer that looks fine and is not.
    llm_max_completion_tokens: int = 512
    llm_max_prompt_tokens: int = 8000
    # Empty string disables the allowlist check.
    llm_model_allowlist: str = "gpt-4o-mini,deepseek-flash"
    # Refuse to call a model absent from the rate table: spend you cannot price cannot be budgeted.
    token_require_priced_model: bool = True
    rag_top_k: int = 4
    rag_min_score: float = 0.10

    # Token-economics attribution tags, the analogue of FinOps cost-allocation tags. They carry no
    # secrets; they exist so a token ledger can answer "which resource spent this".
    workload: str = "ai-platform-proof"
    environment: str = "local-dev"


settings = Settings()