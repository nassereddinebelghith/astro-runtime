from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

class Config(BaseSettings):
    argocd_verify_ssl: bool = Field(default=True, alias="ARGOCD_VERIFY_SSL")
    no_merge: bool = Field(default=False, alias="NO_MERGE")

    @field_validator("*", mode="before")
    def normalize_env(cls, v):
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"", "none", "null"}:
                return None
            if s in {"1", "true", "yes", "on"}:
                return True
            if s in {"0", "false", "no", "off"}:
                return False
        return v

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
    }