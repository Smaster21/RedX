<div align="center">
  <img src="assets/redx-logo.png" alt="RedX Logo" width="250"/>
</div>

# RedX v6.0 — Autonomous Security Orchestrator

**RedX** is an advanced, autonomous AI orchestration platform designed specifically for red team operators, penetration testers, and security researchers. It fuses persistent local memory, real-time web intelligence, and multi-agent reasoning into a single cohesive system that **learns from your engagements, verifies its claims, and writes production-ready exploits.**

[![GitHub repo](https://img.shields.io/badge/Repository-Smaster21/RedX-blue?style=for-the-badge&logo=github)](https://github.com/Smaster21/RedX)
![Version](https://img.shields.io/badge/Version-6.0-red?style=for-the-badge)
![Architecture](https://img.shields.io/badge/Architecture-Native_FastAPI-green?style=for-the-badge)

---

## 🧠 How It Works: The Multi-Brain Architecture

RedX does not rely on a single LLM to guess the answers. Every query passes through a multi-layered intelligence engine:

1. **The Mission Refiner (Prompt Engineering):** Before your raw question hits the main AI, a dedicated lightweight "Secretary" agent intercepts it. It detects ambiguity, adds context, and rewrites your prompt into a highly structured, professional intelligence request to guarantee maximum output quality.
2. **The Secondary Brain (Persistent RAG):** RedX uses a local `ChromaDB` vector database to store everything you teach it. When you ask a question, it instantly retrieves relevant prior research, past exploit data, and local files—giving the AI deep situational awareness without requiring internet access.
3. **The Live Intelligence Engine:** If the answer requires up-to-date 2026 data (e.g., zero-day CVE details), the backend asynchronously scrapes live DuckDuckGo results and injects the raw factual HTML snippets directly into the AI's reasoning loop.
4. **The Main Reasoning Engine:** A frontier model (120B–405B parameters via OpenRouter) analyzes the verified context and generates the final strategic response.

---

## 🚀 Core Capabilities & Important Details

### 1. 🛠️ Sequential Multi-Agent Orchestrator (The Builder)
When you need complex exploit code or infrastructure, a single AI prompt isn't enough. The RedX Builder launches a strict, sequential pipeline of specialized agents:
*   **The Architect** designs the structural attack path.
*   **The Code Engineer** writes the raw Python/Bash exploit based strictly on the Architect's plan.
*   **The Reviewer** acts as a Red Team for the generated code, auditing for race conditions, bugs, and memory leaks.
*   **The Docs Expert** generates the README and usage instructions.
*   **The Synthesizer** merges all their work into one flawless, copy-paste-ready deliverable.

### 2. 🛡️ Strict Scrutiny Mode (Zero-Hallucination)
RedX includes a toggleable "Strict Scrutiny" mode. When enabled, the AI is mathematically barred from guessing. It is forced to **only** reference data retrieved from your local Vault or the live web scrape. If the answer is not in the verified data, it will explicitly refuse to answer, guaranteeing absolute factual accuracy for sensitive assessments.

### 3. 📂 Large Document Vectorization
Instead of dumping 50-page PDF reports into the chat (which poisons the context window and wastes tokens), RedX handles uploads on the backend. When you attach a file, it is chunked, vectorized, and permanently injected into the ChromaDB Vault. The AI can then dynamically recall only the specific paragraphs it needs during the engagement.

### 4. ♾️ Infinite Token Generation
RedX automatically detects when the LLM hits its hard token output limit (e.g., stopping mid-sentence during a long exploit). A clean `Continue Generation` button appears in the UI, allowing you to instantly resume the stream without losing context or missing a single character.

### 5. 🚦 Graceful Rate Limit Handling
Built into the proxy is an **Exponential Backoff Circuit Breaker**. If the AI provider returns a `429 Too Many Requests` or `503 Service Unavailable`, RedX automatically delays and retries up to 10 times, shielding your workflow from API instability.

---

## ⚡ Quick Start Guide

### Prerequisites
*   **Python 3.10+** (Tested on Kali Linux natively)
*   **OpenRouter API Key** (Get one at [OpenRouter.ai](https://openrouter.ai))
*   **No Docker Required:** RedX v6.0 is entirely decoupled and native.

### 1. Installation

Clone the repository and install the required dependencies:
```bash
git clone https://github.com/Smaster21/RedX.git
cd RedX

# Note: Use --break-system-packages if installing on a managed Kali Linux environment
python3 -m pip install -r requirements.txt --break-system-packages
```

### 2. Configuration
Generate a `.env` file to securely store your API constraints:
```bash
cp .env.example .env
```
*(Ensure your `FRONTEND_URL` is set to `http://localhost:8080` in the `.env` file).*

### 3. Launching the Platform
RedX operates on a highly performant, decoupled architecture requiring two terminal sessions.

**Terminal 1: Start the Intelligence Backend (FastAPI Proxy)**
```bash
uvicorn proxy:app --host 127.0.0.1 --port 3000
```

**Terminal 2: Start the Operator UI (Frontend)**
```bash
python3 -m http.server 8080
```

### 4. Initial Setup & Authentication
1. Open your browser and navigate to: `http://localhost:8080`
2. You will be prompted to enter your OpenRouter API Key and create a master vault password.
3. **Security Note:** Your API key is encrypted using AES-256-GCM and stored strictly inside your browser's local storage. It is transmitted securely to the backend via an `X-Proxy-Token` header and is never saved in plain text on the disk.

---

## ⚠️ Important Security Disclaimer
RedX is designed exclusively for authorized penetration testing, red teaming, and educational purposes. Ensure you possess a valid Letter of Authorization (LoA) before utilizing the Multi-Agent Builder against any target. The developers hold no liability for the misuse of this orchestration platform.
