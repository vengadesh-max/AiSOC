from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def legacy_env_alias(name: str) -> AliasChoices:
    """Allow either the current env name or the UEBA-prefixed legacy name."""
    return AliasChoices(name, f"UEBA_{name}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://aisoc:aisoc@localhost:5432/aisoc",
        validation_alias=legacy_env_alias("DATABASE_URL"),
    )

    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        validation_alias=legacy_env_alias("KAFKA_BOOTSTRAP_SERVERS"),
    )
    kafka_input_topic: str = Field(
        default="security.events",
        validation_alias=legacy_env_alias("KAFKA_INPUT_TOPIC"),
    )
    kafka_output_topic: str = Field(
        default="ueba.anomalies",
        validation_alias=legacy_env_alias("KAFKA_OUTPUT_TOPIC"),
    )
    kafka_consumer_group: str = Field(
        default="ueba-service",
        validation_alias=legacy_env_alias("KAFKA_CONSUMER_GROUP"),
    )

    baseline_window_days: int = Field(
        default=30,
        validation_alias=legacy_env_alias("BASELINE_WINDOW_DAYS"),
    )
    anomaly_threshold: float = Field(
        default=3.0,
        validation_alias=legacy_env_alias("ANOMALY_THRESHOLD"),
    )
    peer_group_min_size: int = Field(
        default=3,
        validation_alias=legacy_env_alias("PEER_GROUP_MIN_SIZE"),
    )
    scoring_batch_size: int = Field(
        default=100,
        validation_alias=legacy_env_alias("SCORING_BATCH_SIZE"),
    )

    otel_endpoint: str = Field(
        default="http://localhost:4317",
        validation_alias=legacy_env_alias("OTEL_ENDPOINT"),
    )
    service_name: str = Field(
        default="aisoc-ueba",
        validation_alias=legacy_env_alias("SERVICE_NAME"),
    )

    host: str = Field(
        default="0.0.0.0",
        validation_alias=legacy_env_alias("HOST"),
    )
    port: int = Field(
        default=8004,
        validation_alias=legacy_env_alias("PORT"),
    )


settings = Settings()
