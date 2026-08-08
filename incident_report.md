# Wilston Manufacturing Platform — Incident Investigation Report

**Generated:** 2026-08-08T21:02:22
**Log records analyzed:** 30000 across 3 sources

---

## Executive Summary
[LLM REASONING]
LLM analysis unavailable (Ollama request failed). See raw evidence sections below.

## Incident Summary
[LLM REASONING]
N/A

## Major Issues Detected
[CURRENT EVIDENCE]
| Count | Sources | Error Signature (example) |
|---|---|---|
| 391 | wilston_application.log, wilston_docker.log, wilston_plc.log | Unhandled exception in order processing |
| 389 | wilston_application.log, wilston_docker.log, wilston_plc.log | ECONNRESET while calling payment gateway |
| 370 | wilston_application.log, wilston_docker.log, wilston_plc.log | UnhandledPromiseRejection: TypeError: Cannot read properties of undefined |
| 367 | wilston_application.log, wilston_docker.log, wilston_plc.log | Kafka publish failed |
| 359 | wilston_application.log, wilston_docker.log, wilston_plc.log | Docker container exited unexpectedly |
| 347 | wilston_application.log, wilston_docker.log, wilston_plc.log | JWT verification failed |
| 347 | wilston_application.log, wilston_docker.log, wilston_plc.log | PLC communication timeout |
| 338 | wilston_application.log, wilston_docker.log, wilston_plc.log | MongoNetworkError: connection timed out |
| 337 | wilston_application.log, wilston_docker.log, wilston_plc.log | PostgreSQL error: deadlock detected |
| 328 | wilston_application.log, wilston_docker.log, wilston_plc.log | Redis connection lost |

## Timeline of Correlated Cross-Source Events
[CURRENT EVIDENCE]
| # | Time Window | Sources Involved | Event Count |
|---|---|---|---|
| 1 | 2026-08-01 09:45:00 → 2026-08-01 09:50:00 | wilston_application.log, wilston_docker.log, wilston_plc.log | 249 |
| 2 | 2026-08-01 09:50:00 → 2026-08-01 09:55:00 | wilston_application.log, wilston_docker.log, wilston_plc.log | 240 |
| 3 | 2026-08-01 09:55:00 → 2026-08-01 10:00:00 | wilston_application.log, wilston_docker.log, wilston_plc.log | 236 |
| 4 | 2026-08-01 09:40:00 → 2026-08-01 09:45:00 | wilston_application.log, wilston_docker.log, wilston_plc.log | 232 |
| 5 | 2026-08-01 09:10:00 → 2026-08-01 09:15:00 | wilston_application.log, wilston_docker.log, wilston_plc.log | 230 |
| 6 | 2026-08-01 09:30:00 → 2026-08-01 09:35:00 | wilston_application.log, wilston_docker.log, wilston_plc.log | 225 |
| 7 | 2026-08-01 09:20:00 → 2026-08-01 09:25:00 | wilston_application.log, wilston_docker.log, wilston_plc.log | 224 |
| 8 | 2026-08-01 09:25:00 → 2026-08-01 09:30:00 | wilston_application.log, wilston_docker.log, wilston_plc.log | 224 |

## Root Cause Analysis
[LLM REASONING]
- LLM unavailable

## Supporting Evidence
[LLM REASONING, grounded in CURRENT EVIDENCE]
- See evidence tables below

## Similar Historical Incidents
[HISTORICAL CONTEXT — retrieved via RAG vector search]
- **[HISTORICAL CONTEXT] INC-1008 – Docker daemon unresponsive due to orphaned container buildup** (similarity distance: 0.554)
- **[HISTORICAL CONTEXT] INC-1012 – Container image pull failure due to registry authentication expiry** (similarity distance: 0.575)
- **[HISTORICAL CONTEXT] INC-1015 – Multi-service outage triggered by expired TLS certificate** (similarity distance: 0.650)
- **[HISTORICAL CONTEXT] INC-1004 – Cascading failure from database outage to dependent microservices** (similarity distance: 0.659)

## Recommended Corrective Actions
[LLM REASONING]
- None generated

## Confidence Level
[LLM REASONING]
**Low** — LLM call failed: Failed to get a response from Ollama at http://localhost:11434 after retries: HTTPConnectionPool(host='localhost', port=11434): Read timed out. (read timeout=600)

---
*This report combines three strictly separated sources: programmatically extracted log
evidence ([CURRENT EVIDENCE]), retrieved past incidents ([HISTORICAL CONTEXT]), and
LLM-generated reasoning ([LLM REASONING]). LLM reasoning should be validated by an
engineer before acting on recommendations.*
