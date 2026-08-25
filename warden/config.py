"""Konfigurasi Warden. Semua dari environment (pydantic-settings). Tanpa nilai rahasia di kode."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WARDEN_", env_file=".env", extra="ignore")

    project: str = "warden-local"            # GCP project id (lokal: warden-local + emulator)
    region: str = "us-central1"            # Cloud Run/Firestore
    genai_location: str = "global"           # Vertex Gemini 3.5/3.7 hanya di endpoint global (terverifikasi 25 Agu)
    firestore_db: str = ""                    # kosong = (default); emulator lokal boleh pakai nama lain
    bucket: str = ""                          # gs bucket artefak/log/marker; kosong = lokal (data/gcs/)
    events_topic: str = "warden-events"
    billing_topic: str = "billing-alerts"
    provider: str = "fake"                    # 'gce' | 'fake'
    managed_label: str = "warden-managed"     # hanya mesin berlabel ini yang boleh disentuh
    tick_seconds: int = 120
    ingest_hmac_secret: str = "dev-only-change-me"
    ingest_hmac_secret_prev: str = ""
    investigate_enabled: bool = True          # Investigator agent gathers evidence with read-only tools before diagnosis   # rotasi tanpa downtime: secret lama masih diterima selama masa tenggang
    gemini_model: str = "gemini-3.5-flash"
    gemini_model_lite: str = "gemini-3.5-flash-lite"
    gemini_model_second: str = "gemini-3.7-flash"
    llm_daily_cap_usd: float = 2.0
    auto_spend_daily_cap_usd: float = 10.0
    discord_public_key: str = ""
    discord_bot_token: str = ""
    discord_channel_id: str = ""
    approvers: str = ""                       # id Discord pemilik, dipisah koma
    timezone: str = "Asia/Jakarta"


settings = Settings()
