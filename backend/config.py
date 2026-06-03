from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ollama_host: str = "http://localhost:11434"
    ollama_model_qa: str = "qwen3:14b"
    ollama_model_vl: str = "qwen3-vl:8b"
    ollama_timeout: int = 300

    embed_model: str = "intfloat/multilingual-e5-large"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    data_dir: Path = Path("./data")
    db_path: Path = Path("./data/documind.db")
    upload_dir: Path = Path("./data/uploads")
    screenshot_dir: Path = Path("./data/screenshots")
    image_dir: Path = Path("./data/images")

    enable_multi_perspective: bool = True
    enrich_concurrency: int = 4
    enrich_min_chars: int = 80
    enable_vl_sheet_overview: bool = True
    smart_screenshots: bool = True
    max_cells_per_sheet: int = 20000
    recall_top_n: int = 30
    rerank_top_k: int = 5

    libreoffice_bin: str = "soffice"

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.upload_dir, self.screenshot_dir, self.image_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
