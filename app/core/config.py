"""Environment-driven settings.

Secrets are read from the environment only -- never hardcoded, never logged
(docs/REFERENCES.md S1, S3). ``.env`` is git-ignored; ``.env.example`` ships
placeholders.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- database -----------------------------------------------------------
    #: SQLite by default so the project runs with zero infrastructure. Point this
    #: at postgresql+psycopg://... to run the same models on Postgres.
    database_url: str = "sqlite:///./data/billing.db"

    # --- app ----------------------------------------------------------------
    app_env: str = "development"
    log_level: str = "INFO"

    #: Protects POST /internal/jobs/run so the background job can be demonstrated
    #: on demand without being publicly triggerable.
    internal_job_token: str = "dev-internal-token-change-me"

    # --- stripe (test mode only) -------------------------------------------
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id_pro: str = ""
    #: Where Stripe sends the customer back after a test Checkout.
    stripe_success_url: str = "http://localhost:8000/billing/success"
    stripe_cancel_url: str = "http://localhost:8000/billing/cancel"

    #: Seconds of clock skew tolerated on a webhook timestamp. Stripe's library
    #: default is 300. Never set this to 0 -- that disables the recency check and
    #: re-opens the replay window (rule W3).
    stripe_webhook_tolerance_seconds: int = Field(default=300, gt=0)

    @field_validator("stripe_secret_key")
    @classmethod
    def _refuse_live_mode(cls, v: str) -> str:
        """Hard stop on live keys.

        The brief permits test mode ONLY. A live key here would move real money,
        so the app refuses to start rather than trusting an operator to notice
        (rule S1).
        """
        if v.startswith(("sk_live_", "rk_live_")):
            raise ValueError(
                "Live-mode Stripe key detected. This project is test mode only: "
                "use an sk_test_ key. Refusing to start."
            )
        return v

    @staticmethod
    def _is_real(value: str) -> bool:
        """A value straight out of .env.example is not a configured value.

        Copying .env.example verbatim is the documented first step, so the
        placeholders must read as "unset". Otherwise the app looks configured and
        fails deep inside a Stripe call instead of returning a clear 503.
        """
        return bool(value) and "replace_me" not in value.lower()

    @property
    def stripe_api_configured(self) -> bool:
        return self._is_real(self.stripe_secret_key) and self._is_real(
            self.stripe_price_id_pro
        )

    @property
    def stripe_webhooks_configured(self) -> bool:
        return self._is_real(self.stripe_webhook_secret)

    @property
    def stripe_configured(self) -> bool:
        return self.stripe_api_configured and self.stripe_webhooks_configured


@lru_cache
def get_settings() -> Settings:
    return Settings()
