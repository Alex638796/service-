import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- Bot ---
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))

    # --- MongoDB ---
    MONGO_URI: str = os.getenv("MONGO_URI", "")
    MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "tgforwardbot")

    # --- Forwarding ---
    DEFAULT_DELAY_SECONDS: float = float(os.getenv("DEFAULT_DELAY_SECONDS", "3.0"))

    # --- Topic capture mode expiry (seconds) ---
    TOPIC_CAPTURE_EXPIRY: int = 600  # 10 minutes

    @classmethod
    def validate(cls) -> None:
        """Validate required environment variables on startup."""
        errors = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN is not set")
        if not cls.OWNER_ID:
            errors.append("OWNER_ID is not set or is 0")
        if not cls.MONGO_URI:
            errors.append("MONGO_URI is not set")
        if errors:
            raise EnvironmentError(
                "Missing required environment variables:\n" + "\n".join(f"  - {e}" for e in errors)
            )


config = Config()
