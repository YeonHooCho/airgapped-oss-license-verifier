# Air-Gapped Hybrid Open-Source License Compliance Verification System

> **7th Place out of 24 Teams — 2026 University Capstone Design Competition**  
> **Conference Paper — ICT Platform Society (ICTPS)**  
> A 4-tier on-premise Software Composition Analysis (SCA) pipeline designed for air-gapped environments (0.00 KB external network traffic), combining deterministic AST parsing and offline dependency lookup with harness-guarded local LLM inference.

---

## Overview & Problem Statement
- **Security & Cost Bottlenecks:** Commercial SCA tools (e.g., Black Duck, Snyk) require high licensing fees, while cloud-based LLM APIs introduce proprietary source code leakage risks in air-gapped enterprise or defense networks.
- **Hybrid Two-Track Solution:** This system decouples deterministic package dependency lookup (**Track 1**) from semantic license header inference (**Track 2**), cross-referencing both against the German **OSADL** compatibility matrix to detect legal conflicts locally.

## Repository Structure

```text
├── license-client/     # [Tier 1] React dashboard (file upload, AST/code viewer, OSADL conflict & SBOM/PDF reports)
├── license-server/     # [Tier 2] Java Spring Boot backend (asynchronous orchestration & REST API gateway)
└── capstone/           # [Tier 3·4] Python FastAPI engine, Tree-sitter AST parser, harness guardrails & Ollama client
    ├── main.py             # FastAPI analysis server entrypoint
    ├── rule_judge.py       # Rule-first fast path for standard LICENSE files & SPDX tags
    ├── license_db.py       # High-speed offline SQLite dependency lookup module
    ├── license_compat.py   # German OSADL 119x119 compatibility matrix evaluator
    ├── llm_client.py       # Local Ollama inference client (loopback-bound)
    ├── harness_config.py   # Decoding parameters (temperature=0.0) & guardrail settings
    ├── harness_prompts.py  # Delimiter-isolated prompt templates against injection
    ├── harness_schemas.py  # Structured output schema definitions
    ├── license_refs/       # SPDX, ScanCode, and OSADL reference rule sets
    └── harness_checks/     # Schema validation and pre-flight harness logs
