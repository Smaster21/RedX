# 🔴 RedX v4.2 — Autonomous Operational Intelligence Agent

**RedX** is a security-focused AI orchestration platform built for red team operators, penetration testers, and security researchers. It combines persistent local memory, real-time web intelligence, and frontier AI reasoning into a single autonomous system that **learns, verifies, and never forgets**.

![RedX UI](https://img.shields.io/badge/Version-4.2-red?style=for-the-badge)
![RAG](https://img.shields.io/badge/Memory-Persistent_RAG-blue?style=for-the-badge)
![Intelligence](https://img.shields.io/badge/Learning-Autonomous-purple?style=for-the-badge)
![Search](https://img.shields.io/badge/Search-Live_2026-green?style=for-the-badge)

---

## 🧠 Multi-Brain Architecture

Every query passes through four intelligence layers before a response is generated:

1.  **Secondary Brain (Persistent Memory)**: A local vector database that stores everything RedX has ever learned. Previous recon data, exploit research, and engagement findings are retrieved instantly — no internet required.
2.  **Main Brain (Reasoning Engine)**: Frontier 120B–405B parameter models handle complex logic, exploit chain analysis, and strategic decision-making.
3.  **Third Brain (Live Intelligence)**: Real-time web search pulls the latest zero-days, CVEs, and security tool updates from 2026 sources.
4.  **The Secretary (Prompt Refiner)**: A dedicated lightweight AI agent that rewrites your raw questions into precise, structured security missions — extracting maximum quality from every interaction.

---

## 🚀 Key Features

### 🔬 Deep Analysis Engine
RedX performs **deep structural analysis** on any target source. When you point it at a codebase, tool repository, or documentation:
- Recursively maps the **entire file and folder structure** in seconds.
- Groups results by component, showing every subfolder and file with clear hierarchy.
- Downloads and reads **key documentation files** concurrently for instant comprehension.
- Supports **subdirectory targeting** — analyze a specific folder without scanning the entire project.
- Handles edge cases like special characters in URLs, branch variations, and nested paths automatically.

### 🛡️ Dual-Mode Verification System
A toggle-based intelligence mode that lets you control how the AI thinks:
- **Strict Scrutiny (ON)**: Zero hallucination mode. The AI can **only** reference verified data from its sources and conversation history. If it can't prove the answer, it refuses to guess — guaranteeing absolute accuracy for technical assessments.
- **Standard Reasoning (OFF)**: The AI combines verified data with its full training knowledge for brainstorming, logical inference, and broad explanations.

### 🪄 Mission Refiner
Before your question reaches the main AI, a dedicated "Secretary" agent analyzes it:
- Detects ambiguity and missing context in your prompt.
- Rewrites it into a structured, professional intelligence request.
- The refined version is displayed transparently in the UI so you can see exactly what the AI is working with.
- The main AI then responds to the **optimized** prompt, dramatically improving response quality.

### 🧠 Autonomous Learning
RedX doesn't just answer questions — it **permanently learns** from every interaction:
- **Distills**: Raw search results and web data are compressed into high-density security intelligence.
- **Ingests**: Summaries are stored in the local persistent memory automatically.
- **Recalls**: Future questions are answered using accumulated knowledge — even offline.
- **Deduplicates**: The knowledge base stays lean and clutter-free.

### 🔗 Source Memory
RedX maintains contextual awareness across an entire conversation:
- Automatically recalls previously analyzed sources from your chat history.
- Follow-up questions work naturally — no need to re-paste URLs or repeat context.
- The AI intelligently determines when to re-fetch vs. when to use cached context.

### 📊 Visual Diagram Rendering
Ask for any flowchart, architecture diagram, or attack chain visualization:
- Renders professional diagrams directly inside the chat interface.
- Supports flowcharts, sequence diagrams, state diagrams, and more.
- Dark-mode compatible with the RedX theme.

### 💬 Advanced Chat Management
Full control over your conversation history:
- **✏️ Rename**: Give conversations meaningful names for easy retrieval.
- **🗑️ Delete**: True deletion — removes from both UI and storage instantly.
- **✎ Edit & Retry**: Click any previous message to modify it. The conversation cleanly rewinds to that point, letting you re-branch with a better prompt.

### 🏦 Knowledge Vault
A dedicated interface for managing your AI's persistent memory:
- Browse, search, and inspect every piece of stored intelligence.
- Delete specific knowledge chunks you no longer need.
- Full visibility into what your agent "knows."

---

## 🏗 System Architecture

```
┌──────────────────────────────────────────────────┐
│                   OPERATOR                        │
│              RedX Frontend (UI)                   │
└───────────────────┬──────────────────────────────┘
                    │
┌───────────────────▼──────────────────────────────┐
│            RedX Orchestrator (Backend)            │
│                                                   │
│  ┌─────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ Prompt   │  │ Local    │  │ Live Web       │  │
│  │ Refiner  │  │ Memory   │  │ Intelligence   │  │
│  │ (Llama)  │  │ (RAG DB) │  │ (Search 2026)  │  │
│  └────┬─────┘  └────┬─────┘  └───────┬────────┘  │
│       │              │                │            │
│       └──────────────┼────────────────┘            │
│                      ▼                             │
│  ┌─────────────────────────────────────────────┐  │
│  │         Deep Analysis Engine                 │  │
│  │   (Source Scraping + Structure Mapping)       │  │
│  └──────────────────┬──────────────────────────┘  │
│                     ▼                              │
│  ┌──────────────────────────────────────────┐     │
│  │  Strict/Standard Mode → Context Injection │     │
│  └──────────────────┬───────────────────────┘     │
│                     ▼                              │
│  ┌──────────────────────────────────────────┐     │
│  │     Frontier AI Model (120B–405B)         │     │
│  └──────────────────────────────────────────┘     │
└───────────────────────────────────────────────────┘
```

---

## 🔄 Intelligence Pipeline

```
User Prompt
    │
    ├─ 🪄 Prompt Refiner → Optimized Mission
    │
    ├─ 🧠 Memory Retrieval → Previous findings from local database
    │
    ├─ 📡 Source Detection
    │   ├─ URL detected → Deep Analysis Engine (recursive structure mapping)
    │   ├─ Source Memory → Auto-recall from conversation history
    │   └─ No source → Live Web Intelligence Search
    │
    ├─ 📥 Knowledge Ingestion → Permanent storage
    │
    ├─ 🛡️ Verification Mode (Strict or Standard)
    │
    └─ 🤖 AI Response (Frontier Model)
```

---

## 🛠 Setup & Installation

### Requirements
- **Python**: 3.10 or higher
- **Disk Space**: ~500MB (for local vector storage)
- **API Key**: OpenRouter API key required

### Installation

```bash
# Clone the repository
git clone https://github.com/Smaster21/RedX.git
cd RedX

# Install dependencies
pip install -r requirements.txt
```

### Running RedX

```bash
# Terminal 1: Start the Backend
python3 proxy.py

# Terminal 2: Start the Frontend
python3 -m http.server 8080
```

Open **http://localhost:8080** in your browser.

---

## 📖 Quick Start

1.  **Set API Key**: Open the sidebar → API Vault → Enter your OpenRouter key.
2.  **Choose Mode**: Toggle Strict Scrutiny on/off based on your task.
3.  **Analyze**: Paste a URL or ask any security question.
4.  **Learn**: Watch RedX automatically ingest and store new intelligence.
5.  **Manage**: Use the Knowledge Vault to inspect or prune stored data.

---

## 🔐 Changelog (v4.2)

- ✅ Deep Analysis Engine with recursive structure mapping
- ✅ Mission Refiner (dedicated prompt optimization agent)
- ✅ Dual-Mode Verification (Strict Scrutiny / Standard Reasoning)
- ✅ Source Memory for conversational follow-ups
- ✅ Visual diagram rendering in chat
- ✅ Chat Rename / Delete / Edit-Retry
- ✅ Extended response length (16K tokens)
- 🔧 URL edge-case handling (special characters, branch fallback)
- 🔧 Backend mode-toggle synchronization fix

---

## ⚖️ License & Ethics

RedX is intended for **authorized penetration testing and security research only**. The developers are not responsible for any misuse. Always operate within legal boundaries and with explicit written authorization.

---

**Built for the next generation of autonomous offensive security.** 🔴
