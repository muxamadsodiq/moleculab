import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Single source of truth for the version reported by the API, the User-Agent and report fingerprints.
# Part XVII of the 1.0 directive: this becomes "1.0.0" only once the release gates are met.
APP_VERSION = "0.5.0"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _database_url() -> str:
    # A relative SQLite path resolves against the project root, not the working directory, so starting
    # the app from another folder can never silently create a second, empty database.
    url = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'moleculab.db'}")
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        if path and path != ":memory:" and not Path(path).is_absolute():
            url = f"{prefix}{(ROOT / path).resolve()}"
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str = _database_url()
    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "15"))
    http_max_retries: int = int(os.getenv("HTTP_MAX_RETRIES", "2"))
    contact_email: str = os.getenv("CONTACT_EMAIL", "")
    ncbi_api_key: str = os.getenv("NCBI_API_KEY", "")
    # none | anthropic | claude_cli. "claude_cli" generates through a locally installed, already
    # signed-in Claude Code CLI; it is a local development bridge and must not be used in a public
    # deployment (see app/llm.py ClaudeCodeCLIProvider).
    llm_provider: str = os.getenv("LLM_PROVIDER", "none")
    llm_cli_timeout: float = float(os.getenv("LLM_CLI_TIMEOUT", "120"))
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    # "deterministic" (default) or "llm": add the LLM as a second semantic verifier that can only lower verdicts
    semantic_verifier: str = os.getenv("SEMANTIC_VERIFIER", "deterministic")
    # External HTTP response cache. Screening and repurposing fan out to many provider calls; caching keeps
    # runs fast and inside published usage policies. 0 or HTTP_CACHE=0 disables it.
    http_cache_enabled: bool = os.getenv("HTTP_CACHE", "1") not in ("0", "false", "False", "")
    http_cache_ttl: float = float(os.getenv("HTTP_CACHE_TTL", "86400"))

    @property
    def user_agent(self) -> str:
        contact = f" (mailto:{self.contact_email})" if self.contact_email else ""
        return f"MOLECULAB/{APP_VERSION}{contact}"


settings = Settings()
