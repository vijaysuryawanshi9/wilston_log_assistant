"""
rag_store.py
------------
Builds and queries a local Chroma vector store over the historical incident
knowledge base (historical_incidents.json). This is the [HISTORICAL CONTEXT]
retrieval layer of the RAG pipeline.

Design notes:
- Embeddings: sentence-transformers/all-MiniLM-L6-v2 (local, no API calls).
- Store: ChromaDB, persisted to `.chroma/` so it only needs to be built once.
- Each incident is flattened into one semantically rich text chunk combining
  title, component, error signature, root cause, and resolution steps, so a
  single similarity search captures the whole incident context.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.utils import embedding_functions

from config import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _load_incidents(path: Path = settings.historical_incidents_path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Historical incidents file not found at {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"historical_incidents.json is not valid JSON: {exc}") from exc

    if not isinstance(data, list) or not data:
        raise ValueError("historical_incidents.json must contain a non-empty list of incidents.")
    return data


def _incident_to_text(incident: Dict[str, Any]) -> str:
    """Flatten one incident record into a single searchable text chunk."""
    resolution = "; ".join(incident.get("resolution_steps", []))
    tags = ", ".join(incident.get("tags", []))
    return (
        f"Incident {incident.get('incident_id', 'UNKNOWN')}: {incident.get('title', '')}\n"
        f"Component affected: {incident.get('component_affected', '')}\n"
        f"Error signature: {incident.get('error_signature', '')}\n"
        f"Root cause: {incident.get('root_cause', '')}\n"
        f"Resolution steps: {resolution}\n"
        f"Severity: {incident.get('severity', '')}\n"
        f"Tags: {tags}"
    )


class HistoricalIncidentStore:
    """Wraps a persistent Chroma collection over the historical incident dataset."""

    def __init__(
        self,
        persist_dir: Path = settings.chroma_persist_dir,
        collection_name: str = settings.chroma_collection_name,
        embedding_model_name: str = settings.embedding_model_name,
    ) -> None:
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model_name
        )
        self.client = chromadb.PersistentClient(path=str(persist_dir))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def build(self, incidents_path: Path = settings.historical_incidents_path, force_rebuild: bool = False) -> int:
        """
        Populate the collection from historical_incidents.json.
        Idempotent: if the collection already has entries and force_rebuild
        is False, this is a no-op.
        """
        try:
            existing_count = self.collection.count()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read existing collection count: %s", exc)
            existing_count = 0

        if existing_count > 0 and not force_rebuild:
            logger.info("Vector store already has %d incidents; skipping rebuild.", existing_count)
            return existing_count

        if force_rebuild and existing_count > 0:
            self.client.delete_collection(self.collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn,
                metadata={"hnsw:space": "cosine"},
            )

        incidents = _load_incidents(incidents_path)
        ids = [incident["incident_id"] for incident in incidents]
        documents = [_incident_to_text(incident) for incident in incidents]
        metadatas = [
            {
                "incident_id": incident.get("incident_id", ""),
                "title": incident.get("title", ""),
                "component_affected": incident.get("component_affected", ""),
                "severity": incident.get("severity", ""),
                "tags": ", ".join(incident.get("tags", [])),
            }
            for incident in incidents
        ]

        try:
            self.collection.add(ids=ids, documents=documents, metadatas=metadatas)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to add incidents to vector store: {exc}") from exc

        logger.info("Indexed %d historical incidents into '%s'.", len(incidents), self.collection_name)
        return len(incidents)

    def search(self, query: str, top_k: int = settings.rag_top_k) -> List[Dict[str, Any]]:
        """
        Return the top_k most similar historical incidents for a query string.
        Each result includes the incident metadata, the flattened document
        text, and a similarity distance score (lower = more similar).
        """
        if not query or not query.strip():
            return []

        try:
            results = self.collection.query(query_texts=[query], n_results=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.error("Vector search failed: %s", exc)
            return []

        hits: List[Dict[str, Any]] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for i, doc, meta, dist in zip(ids, docs, metas, distances):
            hits.append(
                {
                    "incident_id": i,
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                }
            )
        return hits


_store_singleton: Optional[HistoricalIncidentStore] = None


def get_store() -> HistoricalIncidentStore:
    """Lazily build (once) and return a shared HistoricalIncidentStore instance."""
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = HistoricalIncidentStore()
        _store_singleton.build()
    return _store_singleton


def retrieve_similar_incidents(query: str, top_k: int = settings.rag_top_k) -> List[Dict[str, Any]]:
    """Public convenience function used by analyzer.py / app.py."""
    store = get_store()
    return store.search(query, top_k=top_k)


if __name__ == "__main__":
    store = get_store()
    example_query = "PLC Modbus communication timeout on production line"
    for hit in store.search(example_query, top_k=3):
        print(f"[{hit['distance']:.3f}] {hit['metadata']['title']}")
