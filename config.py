"""
config.py
---------
Centralized configuration for the Wilston AI-Powered Log Analysis Assistant.

All tunable parameters (paths, model names, RAG parameters, correlation
windows, etc.) live here so the rest of the codebase never hard-codes
"magic values". Import this module and reference `settings` everywhere.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    # ---------------------------------------------------------------- #
    # Paths
    # ---------------------------------------------------------------- #
    base_dir: Path = BASE_DIR
    zip_path: Path = BASE_DIR / "wilston_logs.zip"
    extract_dir: Path = BASE_DIR / "extracted_logs"
    historical_incidents_path: Path = BASE_DIR / "historical_incidents.json"
    chroma_persist_dir: Path = BASE_DIR / ".chroma"
    reports_dir: Path = BASE_DIR / "reports"

    expected_log_files: tuple = (
        "wilston_application.log",
        "wilston_docker.log",
        "wilston_plc.log",
    )

    # ---------------------------------------------------------------- #
    # Ollama / LLM settings
    # ---------------------------------------------------------------- #
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"                # primary model
    ollama_fallback_model: str = "mistral"       # used if primary unavailable
    llm_temperature: float = 0.2
    llm_num_ctx: int = 2048
    llm_request_timeout: int = 600              # seconds
    llm_max_retries: int = 1

    # ---------------------------------------------------------------- #
    # Embeddings / Vector store
    # ---------------------------------------------------------------- #
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    chroma_collection_name: str = "wilston_historical_incidents"
    rag_top_k: int = 4
    rag_score_threshold: float = 0.35   # cosine distance filter (lower=closer, tune per store)

    # ---------------------------------------------------------------- #
    # Log processing
    # ---------------------------------------------------------------- #
    severity_levels: tuple = ("DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL")
    critical_severities: tuple = ("ERROR", "CRITICAL", "FATAL")
    correlation_window_minutes: int = 5   # bin width used to correlate cross-source events
    max_correlation_clusters: int = 10    # top-N busiest cross-source windows to surface as "incidents"
    top_n_error_signatures: int = 15
    max_log_lines_per_source: int = 50_000  # safety guard for very large files

    # ---------------------------------------------------------------- #
    # Report
    # ---------------------------------------------------------------- #
    report_sections: List[str] = field(default_factory=lambda: [
        "Executive Summary",
        "Incident Summary",
        "Major Issues Detected",
        "Timeline of Important Events",
        "Root Cause Analysis",
        "Supporting Evidence",
        "Similar Historical Incidents",
        "Recommended Corrective Actions",
        "Confidence Level",
    ])

    def ensure_dirs(self) -> None:
        """Create any output directories that are missing."""
        self.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.extract_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
 