# CivicProcess — BPMN 2.0 & ISO 27001/27002 Gap Platform

> **GovTech & Critical Infrastructure Operational Modeling and Compliance Intelligence Workbench**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![BPMN 2.0](https://img.shields.io/badge/BPMN-2.0%20Standard-E65100.svg)](https://www.omg.org/spec/BPMN/2.0/)
[![ISO 27002:2022](https://img.shields.io/badge/ISO%2F%20IEC-27002%3A2022%20(93%20Controls)-1D4ED8.svg)](https://www.iso.org/standard/75652.html)
[![GDPR Compliant](https://img.shields.io/badge/GDPR-Privacy%20by%20Design-15803D.svg)](https://gdpr-info.eu/)
[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)

---

## 🏛️ Project Overview

**CivicProcess** is designed for **municipalities, public administrations, and critical infrastructure operators (KRITIS / NIS2)** to bridge the dangerous divide between **formal/legal policy claims** ("what the handbook says") and **actual lived operational practices** ("what staff actually do").

### Core Pillars
1. **Psychological Safety by Design**: Explicitly reassures public sector workers that **gaps are normal and expected**. Bureaucratic theater and dishonest compliance hide genuine vulnerabilities until security incidents strike.
2. **Natural English & Multi-Document Ingestion**: Ingests messy real-world field notes, helpdesk journals, and official handbooks (PDF, DOCX, Markdown, TXT) with **client-side citizen PII scanning and redaction**.
3. **Open-Standard BPMN 2.0 Studio**: Automatically compiles structured process graphs with deterministic horizontal layout coordinates rendered in an interactive, editable canvas powered by `bpmn-js`. Exports standard `.bpmn` XML and `.svg`.
4. **Comprehensive ISO/IEC 27002:2022 Master Checklist (93 Controls)**: Full coverage of all 93 controls across Chapters 5 (Organizational), 6 (People), 7 (Physical), and 8 (Technological).
5. **Pragmatic Risk-Based Compromises**: ISO 27001 explicitly recognizes that one size does not fit all. When strict compliance would paralyze emergency public services, CivicProcess suggests **pragmatic compensating controls** and risk tradeoffs.
6. **BYOK Privacy & 1-Click GDPR Purge**: Zero server key logging (keys stay in browser memory via `X-Gemini-API-Key`) and 1-click cryptographic project wipe under GDPR Art. 17.

---

## 🚀 Quickstart

### Prerequisites
- Python 3.12+ (or Docker)
- Git

### 1. Clone & Setup
```bash
git clone https://github.com/your-username/civicprocess.git
cd civicprocess

# Setup Virtual Environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Populate Datasets & Launch
```bash
# Build the 93-Control ISO 27002:2022 Master Excel and Municipal Benchmarks
python scripts/build_iso_excel.py
python scripts/build_benchmark_data.py

# Start Server
uvicorn app.main:app --reload --port 8000
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

---

## 🚢 Coolify & Cloudflare Tunnel Deployment

CivicProcess ships with a hardened, non-root multi-stage Docker container:

```bash
docker compose up -d --build
```
Point your Cloudflare Tunnel to port `8000` on your Coolify host for domain routing (e.g. `https://oldisserving.live`).
