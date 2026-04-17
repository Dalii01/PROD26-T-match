import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.secret_key = os.getenv("SECRET_KEY", "dev-secret")
        self.db_url = os.getenv(
            "DB_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/tmatch",
        )


settings = Settings()
