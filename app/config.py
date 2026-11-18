"""Settings loaded from environment variables (or a .env file).

Every LLM provider used here speaks the OpenAI-compatible API, so switching from
Gemini to Groq or a local Ollama is a matter of changing LLM_PROVIDER.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Free (or local) providers with an OpenAI-compatible endpoint.
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "chat_model": "gemini-flash-latest",
        "embed_model": "gemini-embedding-001",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "chat_model": "llama-3.3-70b-versatile",
        "embed_model": None,  # Groq has no embeddings endpoint
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "chat_model": "qwen2.5:3b",
        "embed_model": "nomic-embed-text",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "chat_model": "meta-llama/llama-3.3-70b-instruct:free",
        "embed_model": None,
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Chat model
    llm_provider: str = "gemini"  # gemini | groq | ollama | openrouter | fake
    llm_api_key: str = ""
    llm_api_key_ssm_param: str = ""  # on AWS the key is read from SSM Parameter Store
    llm_base_url: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.1

    # Embeddings (can use a different provider than chat, e.g. Groq chat + local embeddings)
    embed_provider: str = "gemini"  # gemini | ollama | local
    embed_api_key: str = ""
    embed_model: str = ""

    # Vector store
    vector_store: str = "numpy"  # numpy | pgvector
    index_dir: str = "data/index"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/assistant"
    s3_bucket: str = ""  # if set, the numpy index is downloaded from S3 at startup
    s3_index_prefix: str = "index/"

    docs_dir: str = "data/docs"
    retrieval_k: int = 4
    retrieval_min_score: float = 0.35

    # Odoo
    odoo_mode: str = "mock"  # mock | live
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_user: str = ""
    odoo_api_key: str = ""

    usage_log: str = "data/usage.jsonl"
    api_token: str = ""  # if set, every API call needs it (X-API-Key header or ?key=)

    def provider(self, name: str) -> dict:
        return PROVIDERS.get(name, {})

    @property
    def chat_base_url(self) -> str:
        return self.llm_base_url or self.provider(self.llm_provider).get("base_url", "")

    @property
    def chat_model(self) -> str:
        return self.llm_model or self.provider(self.llm_provider).get("chat_model", "")

    @property
    def embedding_model(self) -> str:
        return self.embed_model or self.provider(self.embed_provider).get("embed_model") or ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
