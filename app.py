"""
app.py
------
Streamlit UI for the Wilston AI-Powered Log Analysis Assistant.

Tab 1 ("Incident Report"): triggers the full detect -> correlate -> RAG ->
    LLM pipeline and renders the structured investigation report.
Tab 2 ("Log Query Assistant"): a RAG-grounded chatbot that answers natural
    language questions about the logs, with conversation memory and a set
    of one-click example questions covering the assignment's 10 sample
    questions.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

import pandas as pd
import streamlit as st

from config import settings
from ingestion import ingest
from analyzer import (
    run_full_analysis,
    render_markdown_report,
    save_report,
    build_evidence_summary,
    build_historical_context,
    group_error_signatures,
    correlate_events,
    detect_critical_events,
)
from llm_engine import run_qa, OllamaClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Wilston Log Analysis Assistant", layout="wide")

EXAMPLE_QUESTIONS = [
    "What are the most critical errors?",
    "Which service generated the highest number of failures?",
    "Which components experienced repeated failures?",
    "What is the likely root cause of the incident?",
    "Which historical incidents are most similar?",
    "What corrective actions do you recommend?",
    "Show all PostgreSQL-related errors.",
    "How many PLC communication timeout errors occurred?",
    "Which services were affected after the first PLC timeout?",
    "Summarize the overall health of the system.",
]


# --------------------------------------------------------------------------- #
# Cached data loading
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner=False)
def load_logs() -> pd.DataFrame:
    return ingest()


@st.cache_resource(show_spinner=False)
def check_ollama() -> bool:
    return OllamaClient().is_available()


def build_qa_evidence_context(df: pd.DataFrame, question: str) -> str:
    """
    Build a compact evidence context for a Q&A turn: overall stats + the
    log lines most relevant to the question (simple keyword overlap so this
    stays fast and dependency-free; could be swapped for embedding search).
    """
    signature_groups = group_error_signatures(df)
    clusters = correlate_events(df)
    base_summary = build_evidence_summary(df, signature_groups, clusters)

    keywords = [w.lower() for w in question.split() if len(w) > 3]
    if keywords:
        mask = df["message"].str.lower().apply(lambda m: any(k in m for k in keywords))
        matched = df[mask].tail(40)
    else:
        matched = df.tail(0)

    matched_lines = "\n".join(
        f"[{r.timestamp}] ({r.source_file}/{r.component}) {r.severity}: {r.message}"
        for r in matched.itertuples()
    )
    return f"{base_summary}\n\nMost relevant raw log lines for this question:\n{matched_lines or '(none matched by keyword overlap)'}"


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

def main() -> None:
    st.title("🏭 Wilston AI-Powered Log Analysis Assistant")
    st.caption("Multi-source log correlation • RAG over historical incidents • Local LLM via Ollama")

    if not check_ollama():
        st.warning(
            f"Could not reach Ollama at {settings.ollama_base_url}. "
            "Start it with `ollama serve` and ensure a model (e.g. `ollama pull llama3`) is available. "
            "The app will still load logs, but AI-generated sections will show an error."
        )

    try:
        with st.spinner("Loading and parsing log files..."):
            df = load_logs()
    except FileNotFoundError as exc:
        st.error(str(exc))
        st.stop()
    except RuntimeError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.header("Dataset Overview")
        st.metric("Total log records", len(df))
        st.metric("Critical events (ERROR/CRITICAL)", len(detect_critical_events(df)))
        st.write("**Records per source:**")
        st.dataframe(df["source_file"].value_counts().rename("count"), use_container_width=True)
        st.write("**Severity distribution:**")
        st.dataframe(df["severity"].value_counts().rename("count"), use_container_width=True)

    tab_report, tab_chat = st.tabs(["📋 Incident Report", "💬 Log Query Assistant"])

    # ------------------------------------------------------------------- #
    # Tab 1: Incident Report
    # ------------------------------------------------------------------- #
    with tab_report:
        st.subheader("Automated Incident Investigation Report")
        st.caption(
            "Sections are explicitly labeled: **[CURRENT EVIDENCE]** (parsed directly from logs), "
            "**[HISTORICAL CONTEXT]** (retrieved via RAG from past incidents), and "
            "**[LLM REASONING]** (AI-generated analysis)."
        )

        if st.button("🔍 Generate / Refresh Incident Report", type="primary"):
            with st.spinner("Detecting failures, correlating events, retrieving historical context, and running LLM analysis..."):
                result = run_full_analysis(df)
                st.session_state["analysis_result"] = result
                st.session_state["report_paths"] = save_report(df, result)

        result = st.session_state.get("analysis_result")
        if result is None:
            st.info("Click the button above to generate the report.")
        else:
            markdown_report = render_markdown_report(df, result)
            st.markdown(markdown_report)

            paths = st.session_state.get("report_paths", {})
            col1, col2 = st.columns(2)
            if paths.get("markdown"):
                with col1:
                    st.download_button(
                        "⬇️ Download Markdown Report",
                        data=Path(paths["markdown"]).read_text(encoding="utf-8"),
                        file_name="incident_report.md",
                        mime="text/markdown",
                    )
            if paths.get("html"):
                with col2:
                    st.download_button(
                        "⬇️ Download HTML Report",
                        data=Path(paths["html"]).read_text(encoding="utf-8"),
                        file_name="incident_report.html",
                        mime="text/html",
                    )

    # ------------------------------------------------------------------- #
    # Tab 2: Interactive Log Query Assistant
    # ------------------------------------------------------------------- #
    with tab_chat:
        st.subheader("Ask questions about the logs")

        if "chat_history" not in st.session_state:
            st.session_state["chat_history"]: List[dict] = []

        st.write("**Quick questions:**")
        cols = st.columns(2)
        clicked_question = None
        for i, q in enumerate(EXAMPLE_QUESTIONS):
            if cols[i % 2].button(q, key=f"example_q_{i}"):
                clicked_question = q

        for turn in st.session_state["chat_history"]:
            with st.chat_message("user"):
                st.markdown(turn["question"])
            with st.chat_message("assistant"):
                st.markdown(turn["answer"])

        typed_question = st.chat_input("Ask a question about the Wilston logs...")
        question = clicked_question or typed_question

        if question:
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                with st.spinner("Retrieving evidence and generating answer..."):
                    evidence_context = build_qa_evidence_context(df, question)
                    historical_context = build_historical_context(question)
                    try:
                        answer = run_qa(
                            question,
                            evidence_context,
                            historical_context,
                            conversation_history=st.session_state["chat_history"],
                        )
                    except Exception as exc:  # noqa: BLE001
                        answer = f"⚠️ LLM request failed: {exc}"
                st.markdown(answer)

            st.session_state["chat_history"].append({"question": question, "answer": answer})


if __name__ == "__main__":
    main()
