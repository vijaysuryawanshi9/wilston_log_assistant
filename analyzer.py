"""
analyzer.py
-----------
The orchestration layer of the pipeline:
  1. Detects important failures and groups repeated error signatures.
  2. Correlates related events across the three log sources within a
     configurable time window.
  3. Builds strictly-labeled evidence summaries ([CURRENT EVIDENCE]).
  4. Retrieves relevant historical incidents ([HISTORICAL CONTEXT]) via rag_store.
  5. Calls llm_engine for [LLM REASONING] (root cause, recommendations).
  6. Renders everything into a structured Markdown/HTML incident report.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config import settings
from llm_engine import run_incident_analysis
from rag_store import retrieve_similar_incidents

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# --------------------------------------------------------------------------- #
# Failure detection & signature grouping
# --------------------------------------------------------------------------- #

def _signature(message: str) -> str:
    """
    Normalize a log message into an "error signature" by stripping numbers,
    UUIDs, and other high-cardinality tokens, so repeated errors with
    different IDs/timestamps group together.
    """
    sig = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b", "<uuid>", message)  # uuid-like
    sig = re.sub(r"\b\d+\b", "<n>", sig)
    sig = re.sub(r"\s+", " ", sig).strip()
    return sig[:200]


def detect_critical_events(df: pd.DataFrame) -> pd.DataFrame:
    """Return only rows classified as ERROR/CRITICAL (the 'important failures')."""
    return df[df["severity"].isin(settings.critical_severities)].copy()


def group_error_signatures(df: pd.DataFrame, top_n: int = settings.top_n_error_signatures) -> pd.DataFrame:
    """
    Group critical events by normalized error signature, returning the most
    frequent repeated failure patterns with counts, affected sources, and
    first/last occurrence timestamps.
    """
    critical = detect_critical_events(df)
    if critical.empty:
        return pd.DataFrame(columns=["signature", "count", "sources", "components", "first_seen", "last_seen", "example"])

    critical = critical.assign(signature=critical["message"].map(_signature))
    grouped = (
        critical.groupby("signature")
        .agg(
            count=("signature", "size"),
            sources=("source_file", lambda s: sorted(set(s))),
            components=("component", lambda s: sorted(set(s))),
            first_seen=("timestamp", "min"),
            last_seen=("timestamp", "max"),
            example=("message", "first"),
        )
        .reset_index()
        .sort_values("count", ascending=False)
        .head(top_n)
    )
    return grouped


# --------------------------------------------------------------------------- #
# Cross-source correlation
# --------------------------------------------------------------------------- #

@dataclass
class CorrelatedCluster:
    anchor_timestamp: pd.Timestamp
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    events: pd.DataFrame = field(repr=False)

    @property
    def sources_involved(self) -> List[str]:
        return sorted(self.events["source_file"].unique().tolist())

    @property
    def event_count(self) -> int:
        return len(self.events)


def correlate_events(
    df: pd.DataFrame,
    window_minutes: int = settings.correlation_window_minutes,
    max_clusters: int = settings.max_correlation_clusters,
) -> List[CorrelatedCluster]:
    """
    Correlates critical events across multiple log sources using fixed-width
    time bins (default 5 minutes): every critical event is assigned to the
    bin its timestamp falls in, bins touching 2+ distinct log sources are
    kept, and the `max_clusters` busiest (highest event-count) bins are
    returned as the "correlated incident windows".

    This burst-detection approach (rank bins by density, keep the top-N) is
    deliberately used instead of naive chain-merging of any overlapping
    windows: on a dataset where critical events are spread densely and
    continuously across the whole time range (as in the real Wilston logs),
    chain-merging collapses into one meaningless mega-cluster spanning the
    entire log. Fixed bins + ranking instead surface the genuinely busiest,
    most-worth-investigating windows as distinct timeline entries.
    """
    critical = detect_critical_events(df).dropna(subset=["timestamp"]).sort_values("timestamp")
    if critical.empty:
        return []

    freq = f"{window_minutes}min"
    indexed = critical.set_index("timestamp")

    candidate_clusters: List[CorrelatedCluster] = []
    for bin_start, group in indexed.groupby(pd.Grouper(freq=freq)):
        if group.empty:
            continue
        sources_involved = group["source_file"].nunique()
        if sources_involved < 2:
            continue
        events = group.reset_index()
        candidate_clusters.append(
            CorrelatedCluster(
                anchor_timestamp=events["timestamp"].iloc[0],
                window_start=bin_start,
                window_end=bin_start + pd.Timedelta(minutes=window_minutes),
                events=events,
            )
        )

    candidate_clusters.sort(key=lambda c: c.event_count, reverse=True)
    return candidate_clusters[:max_clusters]


# --------------------------------------------------------------------------- #
# Evidence summarization (strictly [CURRENT EVIDENCE], no LLM involved)
# --------------------------------------------------------------------------- #

def build_evidence_summary(df: pd.DataFrame, signature_groups: pd.DataFrame, clusters: List[CorrelatedCluster]) -> str:
    """
    Produces a compact, strictly-factual text block describing the current
    log evidence, suitable for injecting into an LLM prompt or displaying
    directly to the user under a [CURRENT EVIDENCE] label.
    """
    total = len(df)
    severity_counts = df["severity"].value_counts().to_dict()
    per_source_counts = df["source_file"].value_counts().to_dict()
    critical_count = len(detect_critical_events(df))
    critical_by_component = (
        detect_critical_events(df)["component"].value_counts().head(10).to_dict()
    )

    lines = [
        f"Total log records processed: {total}",
        f"Records per source: {per_source_counts}",
        f"Severity distribution: {severity_counts}",
        f"Total critical (ERROR/CRITICAL) events: {critical_count}",
        f"Critical events by component/service (top 10): {critical_by_component}",
        "",
        "Top repeated error signatures:",
    ]
    for _, row in signature_groups.head(10).iterrows():
        lines.append(
            f"  - [{row['count']}x] sources={row['sources']} components={row['components']} "
            f"first={row['first_seen']} last={row['last_seen']} :: {row['example']}"
        )

    lines.append("")
    lines.append(f"Cross-source correlated failure clusters detected: {len(clusters)}")
    for i, cluster in enumerate(clusters[:5], start=1):
        lines.append(
            f"  Cluster {i}: window {cluster.window_start} -> {cluster.window_end}, "
            f"sources={cluster.sources_involved}, events={cluster.event_count}"
        )

    return "\n".join(lines)


def build_correlated_events_summary(clusters: List[CorrelatedCluster], max_events_per_cluster: int = 8) -> str:
    """Detailed, per-cluster breakdown of correlated cross-source events."""
    if not clusters:
        return "No cross-source correlated failure clusters were detected within the configured time window."

    blocks = []
    for i, cluster in enumerate(clusters[:5], start=1):
        block_lines = [f"Cluster {i} ({cluster.window_start} to {cluster.window_end}):"]
        for _, row in cluster.events.head(max_events_per_cluster).iterrows():
            block_lines.append(f"  [{row['timestamp']}] ({row['source_file']}/{row['component']}) {row['severity']}: {row['message']}")
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)


def build_historical_context(query: str, top_k: int = settings.rag_top_k) -> str:
    """Retrieve and format historical incidents for prompt injection / display."""
    hits = retrieve_similar_incidents(query, top_k=top_k)
    if not hits:
        return "No sufficiently similar historical incidents were retrieved."

    lines = []
    for hit in hits:
        meta = hit["metadata"]
        lines.append(
            f"- ({meta.get('incident_id')}) {meta.get('title')} "
            f"[component={meta.get('component_affected')}, severity={meta.get('severity')}, "
            f"similarity_distance={hit['distance']:.3f}]"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Full pipeline + report rendering
# --------------------------------------------------------------------------- #

@dataclass
class IncidentAnalysisResult:
    evidence_summary: str
    correlated_events_summary: str
    historical_context: str
    historical_hits: List[Dict[str, Any]]
    signature_groups: pd.DataFrame
    clusters: List[CorrelatedCluster]
    llm_analysis: Dict[str, Any]
    generated_at: datetime = field(default_factory=datetime.now)


def run_full_analysis(df: pd.DataFrame) -> IncidentAnalysisResult:
    """Runs the complete detect -> correlate -> retrieve -> reason pipeline."""
    signature_groups = group_error_signatures(df)
    clusters = correlate_events(df)

    evidence_summary = build_evidence_summary(df, signature_groups, clusters)
    correlated_events_summary = build_correlated_events_summary(clusters)

    # Build a retrieval query from the top error signatures so RAG search is
    # grounded in what's actually happening in the current logs.
    top_signatures_text = " ".join(signature_groups["example"].head(5).tolist()) if not signature_groups.empty else "system failure"
    historical_hits = retrieve_similar_incidents(top_signatures_text, top_k=settings.rag_top_k)
    historical_context = build_historical_context(top_signatures_text)

    try:
        llm_analysis = run_incident_analysis(evidence_summary, correlated_events_summary, historical_context)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM analysis failed: %s", exc)
        llm_analysis = {
            "executive_summary": "LLM analysis unavailable (Ollama request failed). See raw evidence sections below.",
            "incident_summary": "N/A",
            "root_causes": ["LLM unavailable"],
            "supporting_evidence": [],
            "recommended_actions": [],
            "confidence_level": "Low",
            "confidence_rationale": f"LLM call failed: {exc}",
        }

    return IncidentAnalysisResult(
        evidence_summary=evidence_summary,
        correlated_events_summary=correlated_events_summary,
        historical_context=historical_context,
        historical_hits=historical_hits,
        signature_groups=signature_groups,
        clusters=clusters,
        llm_analysis=llm_analysis,
    )


def render_markdown_report(df: pd.DataFrame, result: IncidentAnalysisResult) -> str:
    """Renders the full structured incident investigation report as Markdown."""
    llm = result.llm_analysis
    root_causes = "\n".join(f"- {c}" for c in llm.get("root_causes", [])) or "- Not determined"
    evidence_bullets = "\n".join(f"- {e}" for e in llm.get("supporting_evidence", [])) or "- See evidence tables below"
    actions = "\n".join(f"- {a}" for a in llm.get("recommended_actions", [])) or "- None generated"

    timeline_lines = []
    for i, cluster in enumerate(result.clusters[:8], start=1):
        timeline_lines.append(
            f"| {i} | {cluster.window_start} → {cluster.window_end} | "
            f"{', '.join(cluster.sources_involved)} | {cluster.event_count} |"
        )
    timeline_table = (
        "| # | Time Window | Sources Involved | Event Count |\n|---|---|---|---|\n" + "\n".join(timeline_lines)
        if timeline_lines else "_No cross-source correlated clusters detected._"
    )

    sig_lines = []
    for _, row in result.signature_groups.head(10).iterrows():
        sig_lines.append(f"| {row['count']} | {', '.join(row['sources'])} | {row['example'][:120]} |")
    sig_table = (
        "| Count | Sources | Error Signature (example) |\n|---|---|---|\n" + "\n".join(sig_lines)
        if sig_lines else "_No repeated critical error signatures detected._"
    )

    hist_lines = []
    for hit in result.historical_hits:
        meta = hit["metadata"]
        hist_lines.append(
            f"- **[HISTORICAL CONTEXT] {meta.get('incident_id')} – {meta.get('title')}** "
            f"(similarity distance: {hit['distance']:.3f})"
        )
    hist_block = "\n".join(hist_lines) or "_No relevant historical incidents retrieved._"

    report = f"""# Wilston Manufacturing Platform — Incident Investigation Report

**Generated:** {result.generated_at.isoformat(timespec="seconds")}
**Log records analyzed:** {len(df)} across {df['source_file'].nunique()} sources

---

## Executive Summary
[LLM REASONING]
{llm.get("executive_summary", "N/A")}

## Incident Summary
[LLM REASONING]
{llm.get("incident_summary", "N/A")}

## Major Issues Detected
[CURRENT EVIDENCE]
{sig_table}

## Timeline of Correlated Cross-Source Events
[CURRENT EVIDENCE]
{timeline_table}

## Root Cause Analysis
[LLM REASONING]
{root_causes}

## Supporting Evidence
[LLM REASONING, grounded in CURRENT EVIDENCE]
{evidence_bullets}

## Similar Historical Incidents
[HISTORICAL CONTEXT — retrieved via RAG vector search]
{hist_block}

## Recommended Corrective Actions
[LLM REASONING]
{actions}

## Confidence Level
[LLM REASONING]
**{llm.get("confidence_level", "Unknown")}** — {llm.get("confidence_rationale", "")}

---
*This report combines three strictly separated sources: programmatically extracted log
evidence ([CURRENT EVIDENCE]), retrieved past incidents ([HISTORICAL CONTEXT]), and
LLM-generated reasoning ([LLM REASONING]). LLM reasoning should be validated by an
engineer before acting on recommendations.*
"""
    return report


def render_html_report(markdown_text: str) -> str:
    """Very light Markdown->HTML wrapper for the report (no external deps required)."""
    try:
        import markdown as md_lib  # optional dependency
        body = md_lib.markdown(markdown_text, extensions=["tables"])
    except ImportError:
        # Fallback: wrap in <pre> if the `markdown` package isn't installed.
        body = f"<pre>{markdown_text}</pre>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Wilston Incident Investigation Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 960px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1, h2 {{ color: #10375c; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 14px; }}
  th {{ background: #f0f4f8; }}
  code, pre {{ background: #f5f5f5; padding: 2px 4px; border-radius: 4px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def save_report(df: pd.DataFrame, result: IncidentAnalysisResult, reports_dir: Path = settings.reports_dir) -> Dict[str, Path]:
    """Renders and writes both Markdown and HTML versions of the report to disk."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    markdown_text = render_markdown_report(df, result)
    html_text = render_html_report(markdown_text)

    md_path = reports_dir / "incident_report.md"
    html_path = reports_dir / "incident_report.html"

    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")

    logger.info("Saved report to %s and %s", md_path, html_path)
    return {"markdown": md_path, "html": html_path}


if __name__ == "__main__":
    from ingestion import ingest

    log_df = ingest()
    analysis_result = run_full_analysis(log_df)
    paths = save_report(log_df, analysis_result)
    print(f"Report written to: {paths['markdown']}")
