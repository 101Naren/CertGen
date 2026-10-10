"""Settings, read from environment variables (or a .env file).

Change a value without touching code, e.g.:
    DATABASE_URL=mysql+pymysql://user:pass@localhost/certgen
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./certgen.db"
    storage_dir: str = "storage"       # where generated PDFs are written
    max_recipients: int = 1000         # upper limit per request
    worker_threads: int = 4            # size of the background thread pool


settings = Settings()
