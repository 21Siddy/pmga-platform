# PMGA — Process Management and Gap Analysis

A lightweight, test-driven framework for extracting Business Process Model and Notation (BPMN 2.0) diagrams from messy real-world operational notes and analyzing compliance gaps against standards like ISO/IEC 27002.

---

## 📌 Project Overview

**PMGA** (Process Management and Gap Analysis) is an exploratory, open-architecture tool designed to bridge the divide between two worlds:
1. **Operational Reality**: How work actually gets done day-to-day (informal notes, shorthand, helpdesk logs, email attachments, and ad-hoc workarounds).
2. **Formal Compliance**: What official handbooks, legal directives, and compliance frameworks (starting with ISO/IEC 27002:2022) require.

The goal is to provide a grounded, practical workbench where non-technical staff and department leads can input honest, unpolished process descriptions, generate standards-compliant BPMN 2.0 diagrams, and review concrete gaps in simple, plain English—without bureaucratic theater or fear of reporting operational shortcuts.

---

## 🎯 Motivation: An Integration of Specialized Components

In modern organizations—especially public administration, municipalities, and critical infrastructure operators—tools for process modeling exist, and compliance checklists exist. However, they almost never talk to each other cleanly.

PMGA is essentially a **sum-of-parts framework**:
- It combines document ingestion, process graph synthesis, deterministic XML layout compilation, and compliance gap evaluation into a unified pipeline.
- While individual concepts (like BPMN modeling or compliance checklists) already exist in isolation, off-the-shelf tools typically assume textbook inputs: clean grammar, idealized flows, and rigid checkboxes. Real operations are messy.
- By connecting these layers under one testable architecture, PMGA highlights where standard AI-driven generation falls short (hallucinations, disconnected diagrams, or missing operational steps) and addresses those shortcomings with deterministic graph validation, semantic recall checks, and compensating control recommendations.

We make no exaggerated claims or promises: this is an evolving, engineering-grounded framework designed to be tested rigorously against real datasets, evaluated across iterations, and improved through disciplined verification.

---

## 🧪 Scientific Verification & End-to-End Testing

To avoid "vibe-coded" pipelines, PMGA evaluates generated process models using deterministic metrics rather than relying on single-shot model outputs:

1. **Structural Soundness ($S_{\text{structural}}$)**:
   - Builds a directed adjacency graph from generated BPMN nodes and edges.
   - Uses graph traversal (Breadth-First Search) to verify that every path originates at a `startEvent`, reaches an `endEvent`, and contains no orphan nodes or deadlock traps.

2. **Semantic Faithfulness ($S_{\text{faithfulness}}$)**:
   - Decomposes messy free-text input (including fragments, shorthand arrows `->`, and German/English mixed logs) into atomic action-object clauses.
   - Evaluates whether the generated BPMN activities actually reflect the operational actions described in the source text.

3. **End-to-End Test Suite**:
   - The platform includes automated tests covering:
     - Document extraction and PII pre-flight redaction.
     - Full 93-control ISO/IEC 27002:2022 checklist parsing and data integrity.
     - Graph validation and BPMN 2.0 XML schema compliance.
     - Complete API workflows from raw text input to annotated Excel (`.xlsx`) and `.bpmn` export.

---

## 🚀 Quickstart (Running Locally)

### Prerequisites
- Python 3.12+
- Git

### 1. Clone & Set Up Environment
```powershell
git clone https://github.com/21Siddy/pmga-platform.git
cd pmga-platform

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install pinned dependencies
pip install -r requirements.txt
```

### 2. Configure Environment (Optional)
Copy the example environment file:
```powershell
Copy-Item .env.example .env
```
You can configure:
- A Google Gemini API key (defaults to `gemini-3.8-flash`), **or**
- An OpenAI-compatible endpoint (such as a local model or university server like Mistral Small 4).
*Note: PMGA also includes a built-in deterministic fallback engine, allowing full end-to-end local testing even without an external API key.*

### 3. Launch the Application
Start the server using the single-command runner:
```powershell
.\run.ps1
```
Or run Uvicorn directly:
```powershell
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```
Open **[http://127.0.0.1:8080](http://127.0.0.1:8080)** in your browser.

---

## 🧪 Running Automated Tests

Run the full automated test suite with verbose output:
```powershell
.\.venv\Scripts\pytest -v
```

---

## 📄 License

This project is licensed under the MIT License.
