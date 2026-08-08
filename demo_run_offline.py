"""
demo_run_offline.py
--------------------
SANDBOX-ONLY DEMO SCRIPT. Not part of the shipped deliverable.

This environment has no network access, so it can't reach Ollama or
download sentence-transformers/ChromaDB. To still produce a real,
end-to-end report from the actual Wilston dataset, this script substitutes:

  - TF-IDF + cosine similarity (scikit-learn, already installed, no
    download needed) in place of the MiniLM-embedding ChromaDB retriever
    for the [HISTORICAL CONTEXT] step.
  - A clearly-labeled template fallback in place of the Ollama call for
    the [LLM REASONING] step (root cause / recommendations / confidence).

Everything else — ingestion, signature grouping, cross-source correlation,
evidence summarization, and report rendering — is the REAL, unmodified
project code (ingestion.py / analyzer.py), running against your real
uploaded log files.

When you run this project normally (`streamlit run app.py` with Ollama
running and internet access for the first-time model downloads), rag_store.py
and llm_engine.py give you the actual embedding-based retrieval and live
LLM reasoning this script can't produce here.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import settings

# --------------------------------------------------------------------------- #
# 1) Stub chromadb so `import rag_store` (pulled in by analyzer.py) succeeds
#    without network access. Real deployments use the actual rag_store.py.
# --------------------------------------------------------------------------- #
chromadb_stub = types.ModuleType("chromadb")


class _DummyCollection:
    def count(self) -> int:
        return 0

    def add(self, *a, **k):
        pass

    def query(self, *a, **k):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


class _DummyClient:
    def get_or_create_collection(self, *a, **k):
        return _DummyCollection()

    def delete_collection(self, *a, **k):
        pass


chromadb_stub.PersistentClient = lambda path: _DummyClient()
utils_stub = types.ModuleType("chromadb.utils")
ef_stub = types.ModuleType("chromadb.utils.embedding_functions")
ef_stub.SentenceTransformerEmbeddingFunction = lambda model_name=None: None
utils_stub.embedding_functions = ef_stub
sys.modules["chromadb"] = chromadb_stub
sys.modules["chromadb.utils"] = utils_stub
sys.modules["chromadb.utils.embedding_functions"] = ef_stub

# --------------------------------------------------------------------------- #
# 2) TF-IDF retriever standing in for rag_store.retrieve_similar_incidents
# --------------------------------------------------------------------------- #

def _load_incidents() -> List[Dict[str, Any]]:
    with open(settings.historical_incidents_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _incident_to_text(incident: Dict[str, Any]) -> str:
    resolution = "; ".join(incident.get("resolution_steps", []))
    tags = ", ".join(incident.get("tags", []))
    return (
        f"{incident.get('title', '')} {incident.get('component_affected', '')} "
        f"{incident.get('error_signature', '')} {incident.get('root_cause', '')} "
        f"{resolution} {tags}"
    )


_INCIDENTS = _load_incidents()
_DOCS = [_incident_to_text(i) for i in _INCIDENTS]
_VECTORIZER = TfidfVectorizer(stop_words="english")
_MATRIX = _VECTORIZER.fit_transform(_DOCS)


def tfidf_retrieve(query: str, top_k: int = 4) -> List[Dict[str, Any]]:
    if not query.strip():
        return []
    q_vec = _VECTORIZER.transform([query])
    sims = cosine_similarity(q_vec, _MATRIX)[0]
    ranked_idx = sims.argsort()[::-1][:top_k]
    hits = []
    for idx in ranked_idx:
        incident = _INCIDENTS[idx]
        hits.append(
            {
                "incident_id": incident["incident_id"],
                "document": _DOCS[idx],
                "metadata": {
                    "incident_id": incident.get("incident_id", ""),
                    "title": incident.get("title", ""),
                    "component_affected": incident.get("component_affected", ""),
                    "severity": incident.get("severity", ""),
                    "tags": ", ".join(incident.get("tags", [])),
                },
                # Report as a "distance" for display consistency with rag_store.py
                # (1 - cosine similarity, so lower = more similar, matching the
                # real ChromaDB cosine-distance convention).
                "distance": float(1 - sims[idx]),
            }
        )
    return hits


# --------------------------------------------------------------------------- #
# 3) Import the REAL project modules and monkeypatch only the retrieval call
# --------------------------------------------------------------------------- #
import analyzer  # noqa: E402  (import after stubbing chromadb)
from ingestion import ingest_from_directory  # noqa: E402

analyzer.retrieve_similar_incidents = tfidf_retrieve


def offline_llm_fallback(evidence_summary: str, correlated_events_summary: str, historical_context: str) -> Dict[str, Any]:
    """
    Template-based substitute for llm_engine.run_incident_analysis.
    Clearly labeled as NOT real LLM reasoning -- this sandbox has no network
    access to reach Ollama. The real project calls the actual local LLM.
    """
    return {
        "executive_summary": (
            "[OFFLINE DEMO -- NOT LLM-GENERATED] This report section is normally produced by a local "
            "Ollama model reasoning over the evidence below. No LLM was reachable in this sandboxed "
            "environment (no network / Ollama server), so this is a placeholder. Run `streamlit run app.py` "
            "locally with `ollama serve` running to see the real generated executive summary."
        ),
        "incident_summary": (
            "[OFFLINE DEMO -- NOT LLM-GENERATED] The evidence sections below (Major Issues Detected, "
            "Timeline, Similar Historical Incidents) are 100% real, computed directly from your uploaded "
            "log files and the historical incident dataset -- only this narrative synthesis step requires "
            "a live LLM connection."
        ),
        "root_causes": ["[OFFLINE DEMO] Not generated -- requires a live Ollama connection."],
        "supporting_evidence": ["See 'Major Issues Detected' and 'Timeline' sections for real, log-derived evidence."],
        "recommended_actions": ["[OFFLINE DEMO] Not generated -- requires a live Ollama connection."],
        "confidence_level": "N/A",
        "confidence_rationale": "LLM unavailable in this sandbox; confidence scoring requires live LLM reasoning.",
    }


analyzer.run_incident_analysis = offline_llm_fallback


def main() -> None:
    print("Loading real Wilston log files...")
    df = ingest_from_directory(settings.extract_dir)
    print(f"Loaded {len(df)} log records across {df['source_file'].nunique()} sources.\n")

    print("Running detection + cross-source correlation + RAG retrieval...")
    result = analyzer.run_full_analysis(df)

    print(f"\nTop error signatures found: {len(result.signature_groups)}")
    print(f"Correlated cross-source incident windows found: {len(result.clusters)}")
    print(f"Historical incidents retrieved (TF-IDF similarity): {len(result.historical_hits)}")
    for hit in result.historical_hits:
        meta = hit["metadata"]
        print(f"  - {meta['incident_id']}: {meta['title']} (distance={hit['distance']:.3f})")

    paths = analyzer.save_report(df, result)
    print(f"\nReport written to:\n  {paths['markdown']}\n  {paths['html']}")


if __name__ == "__main__":
    main()
