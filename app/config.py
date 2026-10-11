from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARGOS_", env_file=ROOT / ".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://argos:argos@127.0.0.1:5432/argos"
    master_key: str = ""
    allowed_roots: list[Path] = [ROOT / "data" / "workspace"]
    data_dir: Path = ROOT / "data"
    cookie_secure: bool = False
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    session_hours: int = Field(ge=1, le=168, default=12)

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("Use PostgreSQL com o driver asyncpg: postgresql+asyncpg://")
        return value

    @field_validator("data_dir", "allowed_roots")
    @classmethod
    def absolute_paths(cls, value):
        def resolve(path):
            return ((ROOT / path) if not path.is_absolute() else path).resolve()

        return [resolve(p) for p in value] if isinstance(value, list) else resolve(value)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="ARGOS", min_length=1, max_length=40)
    language: Literal["pt-BR"] = "pt-BR"
    style: str = Field(default="Natural, objetivo e prestativo", max_length=300)
    max_steps: int = Field(default=16, ge=1, le=100)
    max_calls: int = Field(default=10, ge=1, le=50)
    task_timeout: int = Field(default=300, ge=5, le=3600)
    tool_timeout: int = Field(default=20, ge=1, le=120)
    max_attempts: int = Field(default=2, ge=1, le=3)
    max_concurrent: int = Field(default=2, ge=1, le=4)
    context_chars: int = Field(default=24000, ge=2000, le=100000)
    max_task_cost: float = Field(default=0.10, gt=0, le=100)
    auto_approve_moderate: bool = False
    screenshots_enabled: bool = False
    screenshot_retention_hours: int = Field(default=24, ge=1, le=168)
    event_retention_days: int = Field(default=30, ge=1, le=365)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["deepseek"] = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = Field(default="deepseek-flash", pattern=r"^[a-zA-Z0-9._-]{1,80}$")
    timeout: int = Field(default=60, ge=5, le=180)
    max_tokens: int = Field(default=2048, ge=128, le=8192)
    thinking: bool = False
    vision: bool = True
    tool_calling: bool = True
    structured_output: bool = True
    # Tarifa de pico consultada em 11/10/2026. Conservadora, configurável e não oficial.
    input_price: float = Field(default=0.3, gt=0, le=1000)
    cached_input_price: float = Field(default=0.006, ge=0, le=1000)
    output_price: float = Field(default=1.2, gt=0, le=1000)

    @field_validator("base_url")
    @classmethod
    def official_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if value not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
            raise ValueError("Este adaptador aceita somente o endpoint oficial DeepSeek.")
        return value
