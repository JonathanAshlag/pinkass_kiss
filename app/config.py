from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_backend: Literal["mongodb", "postgres"] = "mongodb"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "pinkas"
    postgres_uri: str = "postgresql+asyncpg://localhost/pinkas"
    openai_base_url: str = "http://localhost:8000/v1"
    openai_api_key: str = "not-needed"
    openai_model: str = "local-model"
    scheduler_hour: int = 3
    scheduler_minute: int = 0
    classification_api_url: str = ""
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
