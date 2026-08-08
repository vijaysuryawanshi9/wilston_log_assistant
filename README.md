# Wilston AI-Powered Log Analysis Assistant

An end-to-end system that ingests multi-source production logs (application,
Docker, and PLC/OT logs), correlates failures across sources, retrieves
relevant historical incidents via RAG, and uses a local LLM (Ollama) to
generate a structured incident investigation report and answer natural
language questions about the logs.

## 1. File Hierarchy

```
wilston_log_assistant/
├── config.py                  # Centralized settings (paths, model names, thresholds)
├── historical_incidents.json  # Mock knowledge base of 15 past incidents (RAG source)
├── ingestion.py                # ZIP extraction + multi-format log parsing/normalization
├── rag_store.py                # Chroma vector store build + similarity search
├── llm_engine.py                # Ollama client, prompt templates, JSON parsing
├── analyzer.py                  # Correlation, evidence summaries, report rendering
├── app.py                        # Streamlit UI (report tab + chatbot tab)
├── make_sample_data.py            # Generates a tiny synthetic wilston_logs.zip for smoke tests
├── requirements.txt
├── reports/                        # Generated incident_report.md / .html land here
├── .chroma/                        # Persisted vector store (auto-created)
└── README.md
```

## 2. Environment Setup

### 2.1 Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com) installed and running locally
- ~4 GB free disk for the local embedding model + an Ollama model

### 2.2 Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2.3 Pull a local LLM via Ollama
```bash
ollama serve                     # start the Ollama server (if not already running)
ollama pull llama3               # primary model (see config.py to change)
ollama pull mistral               # optional fallback model
```

### 2.4 Provide the log data
`wilston_logs.zip` (containing the real `wilston_application.log`,
`wilston_docker.log`, `wilston_plc.log` — 10,000 lines each, 30,000 total)
is already included in the project root, extracted straight from the
assignment's uploaded files. If you're starting from three loose `.log`
files instead of a zip, either re-zip them or call
`ingestion.ingest_from_directory(<dir>)` directly. To smoke-test the
pipeline with a tiny synthetic dataset instead, run:
```bash
python make_sample_data.py   # overwrites wilston_logs.zip with a 20-line sample
```

### 2.5 Runrr

**Generate a report from the CLI:**
```bash
python analyzer.py
# -> writes reports/incident_report.md and reports/incident_report.html
```

**Launch the interactive UI:**
```bash
streamlit run app.py
```
Open the URL Streamlit prints (typically http://localhost:8501). Use the
**Incident Report** tab to generate the full structured report, and the
**Log Query Assistant** tab to ask natural-language questions (including the
10 example questions from the assignment, available as one-click buttons).

## 3. Architecture Overview

```
 wilston_logs.zip
        │
        ▼
 ┌───────────────┐     unified DataFrame       ┌────────────────────┐
 │ ingestion.py   │ ───────────────────────────▶│ analyzer.py         │
 │ (parse/normalize)                             │ - detect failures   │
 └───────────────┘                               │ - group signatures  │
                                                   │ - cross-source      │
                                                   │   correlation (±5m) │
                                                   └─────────┬───────────┘
                                                              │ evidence summary
                          historical_incidents.json           │ ([CURRENT EVIDENCE])
                                   │                            │
                                   ▼                            ▼
                          ┌───────────────┐            ┌────────────────┐
                          │ rag_store.py   │◀──query────│ analyzer.py     │
                          │ Chroma + MiniLM │──results──▶│ (build prompts) │
                          └───────────────┘            └────────┬────────┘
                       ([HISTORICAL CONTEXT])                    │
                                                                  ▼
                                                         ┌────────────────┐
                                                         │ llm_engine.py   │
                                                         │ Ollama (local)  │
                                                         └────────┬────────┘
                                                        ([LLM REASONING])
                                                                  │
                                                                  ▼
                                                         ┌────────────────┐
                                                         │ Markdown/HTML   │
                                                         │ report + Chat UI│
                                                         │   (app.py)      │
                                                         └────────────────┘
```

## 4. Implementation Approach

- **Log format**: all three real Wilston log sources share one structured
  line format: `<ISO8601 timestamp> [SEVERITY] key=value key=value ...
  <free-text message>` (fields: `source`, `service`, `host`, `traceId`, and
  an optional `orderId`). `ingestion.py` uses one generic regex that captures
  every leading `key=value` token (rather than hard-coding field order/
  count, since `orderId` only appears on ~3% of lines) plus a vectorized
  `pd.to_datetime()` pass for the whole file at once. On the real 30,000-line
  dataset this parses in well under a second with zero unparsable lines.
  Every parsed line lands in one unified schema (`timestamp`, `source_file`,
  `log_source`, `component`, `host`, `trace_id`, `order_id`, `severity`,
  `message`, `raw_line`); any line that doesn't match the pattern is still
  preserved (not dropped) with `severity=UNKNOWN`, so no evidence silently
  disappears.
- **Failure grouping**: `analyzer._signature()` normalizes messages (numbers,
  UUIDs) so repeated errors with different IDs/timestamps collapse into one
  signature with a frequency count — this directly satisfies "group similar
  errors together". On the real dataset the top signatures (e.g. "Unhandled
  exception in order processing", "PLC communication timeout") each recur
  300+ times across all three sources and every microservice.
- **Cross-source correlation**: `analyzer.correlate_events` bins critical
  (ERROR/CRITICAL) events into fixed 5-minute windows and ranks bins by
  event density, keeping the top-N busiest windows that touch **2+ distinct
  log sources**. This burst-detection design was chosen deliberately over
  naive "merge any overlapping windows" chaining: on the real Wilston data,
  critical events are dense and continuous across the whole ~84-minute
  capture, so chain-merging degenerates into one meaningless mega-cluster
  spanning the entire log. Ranking fixed bins instead surfaces genuinely
  distinct, worth-investigating timeline entries (e.g. the busiest window
  was 09:45–09:50 with 249 correlated critical events across all 3 sources).
- **RAG**: `historical_incidents.json` (15 incidents) is flattened into
  semantically rich text chunks and embedded with
  `sentence-transformers/all-MiniLM-L6-v2` into a persisted ChromaDB
  collection (`rag_store.py`). Retrieval queries are built from the current
  log evidence itself (top error signatures / the user's question), so
  retrieval is grounded in what's actually happening.
- **Strict attribution**: every prompt to the LLM explicitly separates
  `CURRENT LOG EVIDENCE` (programmatic, from `ingestion.py`/`analyzer.py`)
  from `HISTORICAL INCIDENT CONTEXT` (from `rag_store.py`), and the LLM is
  instructed never to fabricate either. The rendered report and the app UI
  both visually tag every section as **[CURRENT EVIDENCE]**,
  **[HISTORICAL CONTEXT]**, or **[LLM REASONING]**.
- **Prompt management**: `llm_engine.py` centralizes all prompt templates and
  forces structured output (JSON for the report, Markdown for Q&A) with a
  strict system prompt banning conversational filler; `extract_json()` is
  tolerant of stray code fences so parsing doesn't break on minor format
  drift.
- **Interactive assistant**: `app.py`'s chat tab re-runs retrieval per
  question (both log evidence via keyword-overlap matching and historical
  incidents via vector search), keeps a rolling conversation history for
  context, and ships one-click buttons for the 10 assignment example
  questions.

## 5. Notes / Extensibility

- Swap the keyword-overlap evidence matching in `app.py` for a proper
  embedding index over log lines (e.g. a second Chroma collection) if you
  need semantic (not just keyword) log retrieval — the RAG scaffolding in
  `rag_store.py` generalizes directly to that.
- `config.py` is the single place to change model names, the correlation
  window, top-K retrieval, and severity thresholds.
- All modules are independently runnable/testable (`python ingestion.py`,
  `python rag_store.py`, `python analyzer.py`).

---

## 6. Presentation Template (5–8 slides)

Use these as slide-by-slide speaker notes / bullet starting points.

**Slide 1 — Problem Understanding**
- Manufacturing platform generates ~30k log lines/day across app, Docker,
  and PLC/OT sources; diagnosing incidents manually is slow and error-prone.
- Goal: automated, evidence-grounded incident analysis + interactive Q&A.

**Slide 2 — Solution Overview**
- Ingest → correlate → retrieve historical context → LLM reasoning → report
  + chatbot, all running locally (Ollama + Chroma, no external API calls).

**Slide 3 — System Architecture**
- (Insert the architecture diagram from Section 3 above.)

**Slide 4 — AI Pipeline**
- Local embeddings (MiniLM) + local LLM (Ollama/llama3) + strict prompt
  templates that force structured JSON/Markdown output.

**Slide 5 — RAG Workflow**
- 15-incident historical knowledge base → flattened to text chunks →
  embedded → ChromaDB → top-K similarity search seeded by current log
  evidence (not just the raw user question).

**Slide 6 — Design Decisions**
- Strict [CURRENT EVIDENCE] / [HISTORICAL CONTEXT] / [LLM REASONING]
  labeling to prevent hallucinated evidence from being mistaken for fact.
- Time-window cross-source correlation instead of naive per-file analysis.

**Slide 7 — Challenges & Key Learnings**
- Handling heterogeneous log formats without dropping unparsable lines.
- Keeping LLM output reliably structured (JSON mode + tolerant parsing).

**Slide 8 — Future Improvements**
- Embedding-based log retrieval (not just keyword overlap) for Q&A.
- Re-ranking of RAG hits, agent-based multi-step investigation, eval harness.

---

### Reflection Questions

**1. What technical achievement are you most proud of?**
I am most proud of building the Wilston AI-Powered Log Analysis Assistant. I worked on the complete pipeline, from log ingestion and error detection to cross-source correlation, RAG-based historical incident retrieval, and local LLM-based root-cause analysis. I also integrated Ollama and ChromaDB and developed a Streamlit interface for interactive log analysis.

**2. What technical skill are you currently improving?**
I am currently improving my Generative AI and LLM skills, especially RAG pipelines, prompt engineering, vector databases, and building reliable AI applications. I am also improving my Python skills and learning how to design production-ready AI systems.

**3. What kind of engineering work excites you the most?**
ReI am most excited about solving real-world engineering problems using AI and machine learning. I particularly enjoy working on systems where data, automation, and AI can improve efficiency, detect problems early, and support better decision-making.

**4. What projects or technical areas would you like to work on over the next three years?**
Over the next three years, I would like to work on advanced AI/ML and Generative AI projects, especially RAG systems, LLM applications, Agentic AI, predictive maintenance, computer vision, and intelligent automation. My goal is to build scalable, production-ready AI solutions that solve practical business and engineering problems.
