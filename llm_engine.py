"""
llm_engine.py
-------------
Encapsulates all communication with the local Ollama LLM server, plus the
prompt templates used across the app. Every prompt instructs the model to
return either strict JSON or clean Markdown with no conversational filler,
and every call site clearly labels output as [LLM REASONING] downstream.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests

from config import settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class LLMEngineError(Exception):
    """Raised when the LLM cannot produce a usable response after retries."""


class OllamaClient:
    """Thin wrapper around the Ollama /api/generate and /api/chat endpoints."""

    def __init__(
        self,
        base_url: str = settings.ollama_base_url,
        model: str = settings.ollama_model,
        fallback_model: str = settings.ollama_fallback_model,
        temperature: float = settings.llm_temperature,
        timeout: int = settings.llm_request_timeout,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fallback_model = fallback_model
        self.temperature = temperature
        self.timeout = timeout

    def is_available(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def _resolve_model(self) -> str:
        """Pick the primary model if available locally, else fall back."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            available = {m["name"].split(":")[0] for m in resp.json().get("models", [])}
            if self.model.split(":")[0] in available:
                return self.model
            if self.fallback_model.split(":")[0] in available:
                logger.warning("Primary model '%s' not found; using fallback '%s'.", self.model, self.fallback_model)
                return self.fallback_model
            if available:
                chosen = sorted(available)[0]
                logger.warning("Neither primary nor fallback model found; using '%s'.", chosen)
                return chosen
        except requests.RequestException as exc:
            logger.warning("Could not query available Ollama models: %s", exc)
        return self.model

    def generate(self, prompt: str, system: Optional[str] = None, json_mode: bool = False) -> str:
        """
        Call Ollama's generate endpoint. Retries on transient failures.
        Returns the raw text response (caller is responsible for parsing).
        """
        model = self._resolve_model()
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "system": system or "",
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": settings.llm_num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"

        last_exc: Optional[Exception] = None
        for attempt in range(1, settings.llm_max_retries + 2):
            try:
                resp = requests.post(
                    f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "").strip()
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("Ollama call failed (attempt %d/%d): %s", attempt, settings.llm_max_retries + 1, exc)

        raise LLMEngineError(
            f"Failed to get a response from Ollama at {self.base_url} after retries: {last_exc}"
        )


def extract_json(text: str) -> Dict[str, Any]:
    """
    Best-effort extraction of a JSON object from an LLM response, tolerant of
    accidental markdown code fences or leading/trailing prose.
    """
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise LLMEngineError(f"Could not parse JSON from LLM response: {exc}") from exc
        raise LLMEngineError("LLM response did not contain a parseable JSON object.")


# --------------------------------------------------------------------------- #
# Prompt templates
# --------------------------------------------------------------------------- #

SYSTEM_ANALYST = (
    "You are a senior Site Reliability Engineer and root-cause-analysis expert "
    "for an industrial manufacturing platform (application services, Docker "
    "containers, and PLC/OT controllers). You write precise, technically "
    "grounded incident analysis. You NEVER invent facts not present in the "
    "evidence provided to you. You NEVER include conversational filler, "
    "apologies, or meta-commentary about being an AI. Output ONLY what is "
    "requested, in the exact format requested."
)

_FEW_SHOT_JSON_EXAMPLE = """
Example output format (structure only — do not reuse these example values):
{
  "executive_summary": "One short paragraph summarizing the incident for a non-technical audience.",
  "incident_summary": "One short paragraph summarizing what happened technically.",
  "root_causes": ["Most likely root cause stated as one sentence.", "Secondary possible cause, if any."],
  "supporting_evidence": ["Specific log-derived fact 1", "Specific log-derived fact 2"],
  "recommended_actions": ["Actionable recommendation 1", "Actionable recommendation 2"],
  "confidence_level": "High | Medium | Low",
  "confidence_rationale": "One sentence explaining the confidence score."
}
"""


def build_incident_analysis_prompt(
    evidence_summary: str,
    correlated_events_summary: str,
    historical_context: str,
) -> str:
    """
    Builds the primary root-cause-analysis prompt. `evidence_summary` and
    `correlated_events_summary` come strictly from parsed logs ([CURRENT
    EVIDENCE]); `historical_context` comes strictly from the RAG retriever
    ([HISTORICAL CONTEXT]). The LLM is asked to reason over both and clearly
    separate its own inferences ([LLM REASONING]).
    """
    return f"""
You will analyze a production incident using two strictly separated evidence sources.

=== CURRENT LOG EVIDENCE (ground truth, extracted programmatically from logs) ===
{evidence_summary}

=== CORRELATED CROSS-SOURCE EVENTS (ground truth, computed programmatically) ===
{correlated_events_summary}

=== HISTORICAL INCIDENT CONTEXT (retrieved via vector similarity search, may or may not be directly applicable) ===
{historical_context}

TASK:
Using ONLY the evidence above, produce a root cause analysis. Do not fabricate
log lines, timestamps, or historical incidents that were not provided to you.
If the evidence is insufficient to be certain, say so explicitly and lower the
confidence_level accordingly.

{_FEW_SHOT_JSON_EXAMPLE}

Respond with ONLY the JSON object. No preamble, no markdown fences, no explanation outside the JSON.
""".strip()


def build_qa_prompt(
    question: str,
    evidence_context: str,
    historical_context: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Builds a prompt for the interactive log query assistant (RAG-grounded Q&A)."""
    history_block = ""
    if conversation_history:
        turns = "\n".join(
            f"User: {turn['question']}\nAssistant: {turn['answer']}" for turn in conversation_history[-3:]
        )
        history_block = f"\n=== RECENT CONVERSATION (for context only) ===\n{turns}\n"

    return f"""
You are answering a question about a production incident. Ground your answer
strictly in the evidence provided below. If the evidence does not contain the
answer, say so plainly instead of guessing.
{history_block}
=== CURRENT LOG EVIDENCE ===
{evidence_context}

=== HISTORICAL INCIDENT CONTEXT (only relevant if the question references past/similar incidents) ===
{historical_context}

=== QUESTION ===
{question}

Respond with a concise, well-structured Markdown answer (use bullet points or
a short table where appropriate). Do not include a preamble like "Sure, here is...".
""".strip()


def run_incident_analysis(
    evidence_summary: str, correlated_events_summary: str, historical_context: str
) -> Dict[str, Any]:
    """High-level helper: build prompt, call LLM in JSON mode, parse result."""
    client = OllamaClient()
    prompt = build_incident_analysis_prompt(evidence_summary, correlated_events_summary, historical_context)
    raw = client.generate(prompt, system=SYSTEM_ANALYST, json_mode=True)
    return extract_json(raw)


def run_qa(
    question: str,
    evidence_context: str,
    historical_context: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """High-level helper for the interactive Q&A assistant. Returns Markdown text."""
    client = OllamaClient()
    prompt = build_qa_prompt(question, evidence_context, historical_context, conversation_history)
    return client.generate(prompt, system=SYSTEM_ANALYST, json_mode=False)
