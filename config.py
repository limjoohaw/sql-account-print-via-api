"""Application configuration from .env file."""

import os
import sys
from pydantic_settings import BaseSettings

# App directory: next to .exe in frozen mode, next to this file in dev mode
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve(path: str) -> str:
    """Resolve a relative path against APP_DIR."""
    if os.path.isabs(path):
        return path
    return os.path.join(APP_DIR, path)


class Settings(BaseSettings):
    sqlacc_aws_region: str = "ap-southeast-1"
    session_secret: str = "change-me-to-64-random-hex-chars"
    log_dir: str = "./logs"
    doc_types_file: str = "./doc_types.json"
    companies_file: str = "./companies.json"
    users_file: str = "./users.json"
    default_templates_file: str = "./default_templates.json"

    class Config:
        env_file = os.path.join(APP_DIR, ".env")
        env_file_encoding = "utf-8"

    @property
    def log_dir_resolved(self) -> str:
        return _resolve(self.log_dir)

    @property
    def doc_types_path(self) -> str:
        return _resolve(self.doc_types_file)

    @property
    def companies_path(self) -> str:
        return _resolve(self.companies_file)

    @property
    def users_path(self) -> str:
        return _resolve(self.users_file)

    @property
    def default_templates_path(self) -> str:
        return _resolve(self.default_templates_file)


settings = Settings()
