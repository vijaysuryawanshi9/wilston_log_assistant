"""
ingestion.py
------------
Handles extraction of the wilston_logs.zip archive and normalization of the
three log sources (application, docker, plc) into a single unified pandas
DataFrame.

All three Wilston log sources share ONE structured line format:

    <ISO8601 timestamp> [<SEVERITY>] key1=value1 key2=value2 ... <free-text message>

    e.g.
    2026-08-01T09:00:02.214000Z [ERROR] source=application service=auth-service
        host=wilston-prod-02 traceId=trace-781453  PLC communication timeout

The set of key=value fields is not fixed-position (an optional `orderId=`
field appears on some lines between `traceId` and the message), so the
parser generically captures every leading `key=value` token rather than
hard-coding field order.

Unified schema:
    timestamp     : pandas.Timestamp (tz-naive, ISO-parsed)
    source_file   : str  ("wilston_application.log" | "wilston_docker.log" | "wilston_plc.log")
    log_source    : str  (the line's own `source=` field, e.g. "application"/"docker"/"plc")
    component     : str  (the line's `service=` field, e.g. "order-service")
    host          : str  (the line's `host=` field, e.g. "wilston-prod-01")
    trace_id      : str  (the line's `traceId=` field, if present)
    order_id      : str  (the line's `orderId=` field, if present, else "")
    severity      : str  (INFO / WARN / ERROR / CRITICAL / UNKNOWN)
    message       : str  (free-text remainder of the log line)
    raw_line      : str  (original untouched line, for traceability)
"""

from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from config import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# --------------------------------------------------------------------------- #
# Single unified pattern: all three Wilston log sources share this structure.
# Generic key=value capture handles the variable/optional field set (e.g.
# `orderId=` only appears on a subset of lines) without hard-coding order.
# --------------------------------------------------------------------------- #
LOG_LINE_PATTERN = re.compile(
    r"^(?P<timestamp>\S+)\s+"
    r"\[(?P<severity>[A-Z]+)\]\s+"
    r"(?P<kvs>(?:[A-Za-z_][\w]*=\S+\s+)+)"
    r"(?P<message>.+)$"
)

KV_PATTERN = re.compile(r"([A-Za-z_][\w]*)=(\S+)")

SEVERITY_NORMALIZATION = {
    "WARNING": "WARN",
    "FATAL": "CRITICAL",
    "ERR": "ERROR",
}


@dataclass
class ParsedLine:
    timestamp: Optional[pd.Timestamp]
    source_file: str
    log_source: str
    component: str
    host: str
    trace_id: str
    order_id: str
    severity: str
    message: str
    raw_line: str


def extract_zip(zip_path: Path = settings.zip_path, extract_dir: Path = settings.extract_dir) -> Path:
    """
    Extract the wilston_logs.zip archive into `extract_dir`.
    Streams the extraction (no full in-memory read of the archive contents)
    and is safe to call repeatedly (idempotent).
    """
    extract_dir.mkdir(parents=True, exist_ok=True)

    if not zip_path.exists():
        raise FileNotFoundError(
            f"Could not find log archive at {zip_path}. "
            f"Place 'wilston_logs.zip' in the project root before running ingestion."
        )

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                # Guard against zip-slip path traversal: only use the base filename.
                target_path = extract_dir / Path(member.filename).name
                with zf.open(member) as source, open(target_path, "wb") as target:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
        logger.info("Extracted archive to %s", extract_dir)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"'{zip_path}' is not a valid zip archive: {exc}") from exc

    return extract_dir


def _normalize_severity(raw_severity: Optional[str]) -> str:
    if not raw_severity:
        return "UNKNOWN"
    sev = raw_severity.strip().upper()
    if sev in SEVERITY_NORMALIZATION:
        return SEVERITY_NORMALIZATION[sev]
    return sev if sev in settings.severity_levels else "UNKNOWN"


def _parse_kvs(kv_blob: str) -> Dict[str, str]:
    return {k: v for k, v in KV_PATTERN.findall(kv_blob)}


def parse_log_file(file_path: Path, source_name: str) -> List[ParsedLine]:
    """
    Parse a single log file into a list of ParsedLine records. Lines that
    don't match the expected structured pattern are still preserved (with
    UNKNOWN severity/component) rather than dropped, so no evidence is lost.

    Timestamp strings are collected during the line-by-line pass but parsed
    in one vectorized `pd.to_datetime()` call at the end (rather than once
    per line) since per-call timestamp parsing overhead dominates runtime on
    files with tens of thousands of lines.
    """
    records: List[ParsedLine] = []
    raw_timestamps: List[Optional[str]] = []

    if not file_path.exists():
        logger.warning("Log file not found: %s", file_path)
        return records

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                if line_no > settings.max_log_lines_per_source:
                    logger.warning(
                        "Reached max_log_lines_per_source (%s) for %s; truncating.",
                        settings.max_log_lines_per_source, source_name,
                    )
                    break

                match = LOG_LINE_PATTERN.match(line)
                if match:
                    gd = match.groupdict()
                    kvs = _parse_kvs(gd.get("kvs", ""))
                    raw_timestamps.append(gd.get("timestamp"))
                    records.append(
                        ParsedLine(
                            timestamp=None,  # filled in below via vectorized parse
                            source_file=source_name,
                            log_source=kvs.get("source", "unknown"),
                            component=kvs.get("service", "unknown"),
                            host=kvs.get("host", "unknown"),
                            trace_id=kvs.get("traceId", ""),
                            order_id=kvs.get("orderId", ""),
                            severity=_normalize_severity(gd.get("severity")),
                            message=(gd.get("message") or line).strip(),
                            raw_line=line,
                        )
                    )
                else:
                    # Fallback record: keep the raw text so nothing is lost.
                    raw_timestamps.append(None)
                    records.append(
                        ParsedLine(
                            timestamp=None,
                            source_file=source_name,
                            log_source="unknown",
                            component="unknown",
                            host="unknown",
                            trace_id="",
                            order_id="",
                            severity="UNKNOWN",
                            message=line.strip(),
                            raw_line=line,
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed parsing %s: %s", file_path, exc)
        return records

    if records:
        parsed_ts = pd.to_datetime(pd.Series(raw_timestamps), errors="coerce", utc=True)
        parsed_ts = parsed_ts.dt.tz_localize(None)
        for record, ts in zip(records, parsed_ts):
            record.timestamp = None if pd.isna(ts) else ts

    return records


def load_all_logs(extract_dir: Path = settings.extract_dir) -> pd.DataFrame:
    """
    Parse all three expected log sources and return a single normalized,
    time-sorted DataFrame. Missing files are skipped with a warning rather
    than raising, so partial datasets can still be analyzed.
    """
    all_records: List[ParsedLine] = []

    for source_name in settings.expected_log_files:
        file_path = extract_dir / source_name
        records = parse_log_file(file_path, source_name)
        logger.info("Parsed %d lines from %s", len(records), source_name)
        all_records.extend(records)

    if not all_records:
        raise RuntimeError(
            "No log records were parsed. Verify that wilston_logs.zip was extracted "
            "and contains the expected files: " + ", ".join(settings.expected_log_files)
        )

    df = pd.DataFrame([r.__dict__ for r in all_records])

    # Forward/backward-fill missing timestamps using neighboring rows within the
    # same source file so ordering / correlation is not broken by unparsable lines.
    df["timestamp"] = df.groupby("source_file")["timestamp"].transform(lambda s: s.ffill().bfill())
    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)

    logger.info("Loaded %d total log records across %d sources", len(df), df["source_file"].nunique())
    return df


def ingest(zip_path: Path = settings.zip_path) -> pd.DataFrame:
    """Convenience entrypoint: extract + parse + return unified DataFrame."""
    extract_dir = extract_zip(zip_path)
    return load_all_logs(extract_dir)


def ingest_from_directory(directory: Path) -> pd.DataFrame:
    """
    Alternate entrypoint for when the three log files are already sitting in
    a directory (e.g. uploaded individually rather than as a zip).
    """
    return load_all_logs(directory)


if __name__ == "__main__":
    dataframe = ingest()
    print(dataframe.head(20))
    print(dataframe["severity"].value_counts())
