<div align="center">
  <img src="assets/redx-logo.png" alt="RedX Logo" width="250"/>
</div>

# RedX v6.0 — Autonomous Security Orchestrator

**RedX** is an advanced, autonomous AI orchestration platform designed for red team operators, penetration testers, and security researchers. It fuses persistent local memory, real-time web intelligence, and multi-agent reasoning into a single cohesive system that **learns from your engagements, verifies its claims, and writes production-ready exploits.**

[![GitHub repo](https://img.shields.io/badge/Repository-Smaster21/RedX-blue?style=for-the-badge&logo=github)](https://github.com/Smaster21/RedX)
![Version](https://img.shields.io/badge/Version-6.0-red?style=for-the-badge)
![Architecture](https://img.shields.io/badge/Architecture-Native_FastAPI-green?style=for-the-badge)

---

## ⚡ Quick Start (3 Steps)

```bash
# 1. Clone & Install
git clone https://github.com/Smaster21/RedX.git && cd RedX
python3 -m pip install -r requirements.txt --break-system-packages

# 2. Configure
cp .env.example .env

# 3. Launch (two terminals)
python3 -m uvicorn proxy:app --host 127.0.0.1 --port 3000   # Terminal 1: Backend
python3 -m http.server 8080                                   # Terminal 2: Frontend
```

Open `http://localhost:8080` → Enter your [OpenRouter API Key](https://openrouter.ai) → Start hacking.

---

## 🧠 How It Works: The Intelligence Pipeline

Every query passes through a **5-stage pipeline** before reaching the LLM:

```
User Query
    │
    ├─① Mission Refiner ──→ Rewrites vague prompts into structured intel requests
    │
    ├─② Secondary Brain ──→ Searches local ChromaDB for prior research & context
    │
    ├─③ Live Search ──────→ DuckDuckGo API → HTML scrape fallback → real-time data
    │
    ├─④ Knowledge Distiller → Cleans raw search data via lightweight LLM pool
    │       └─ Stores verified intel permanently in the vault
    │
    └─⑤ Main Reasoning ──→ Frontier model (120B–405B) synthesizes final response
```

### Key Design Decisions

| Component | Technology | Why |
|---|---|---|
| Vector DB | ChromaDB + `all-MiniLM-L6-v2` | Fast local embeddings, no cloud dependency |
| Knowledge Graph | NetworkX (MultiDiGraph) | Maps entity relationships (CVE → Tool → Technique) |
| Search Engine | `ddgs` (DuckDuckGo) + HTML scrape | Dual-layer fallback guarantees results |
| API Gateway | FastAPI + aiohttp streaming | Non-blocking SSE for real-time token streaming |
| Encryption | AES-256-GCM (browser-side) | API key never stored in plain text on disk |

---

## 🛠️ Core Features

### 1. Multi-Agent Builder (5 AI Specialists)
Complex tasks like writing exploits trigger a **sequential agent pipeline**:

| Agent | Role | Primary Model |
|---|---|---|
| 🔧 Code Engineer | Writes raw exploit code | `qwen/qwen3-coder:free` |
| 🏗️ Architect | Designs the attack structure | `poolside/laguna-m.1:free` |
| 📚 Docs Expert | Generates documentation | `openai/gpt-oss-120b:free` |
| 🔍 Code Reviewer | Audits for bugs & race conditions | `deepseek/deepseek-v4-flash:free` |
| ⚙️ Integrator | Handles dependencies & deployment | `nvidia/nemotron-3-super-120b-a12b:free` |

Each agent has **3 fallback models** — if one is rate-limited, it automatically cascades to the next.

### 2. Secondary Brain (Persistent RAG Vault)
- Every chat **automatically ingests** verified web data into local ChromaDB
- Uploaded documents are chunked, vectorized, and permanently stored
- Knowledge graph tracks entity relationships across engagements
- Data persists across sessions — RedX remembers everything

### 3. Strict Scrutiny Mode (Zero-Hallucination)
When enabled, the AI is forced to **only** reference data from the local vault or live web scrape. If the answer isn't in verified data, it explicitly refuses rather than guessing.

### 4. Live Web Intelligence
- **DDG API** → Primary search (fast, structured results)
- **HTML Scrape** → Fallback when API fails
- **GitHub Deep Scrape** → Full repository tree + file contents for URL targets

### 5. Resilient Rate Limit Handling
- **Exponential backoff** with up to 10 retries per request
- **429/503 auto-retry** — shields your workflow from API instability
- **Multi-model fallback chains** — if one model is busy, the next one takes over

### 6. Infinite Token Generation
Detects when the LLM hits its output limit mid-sentence. A `Continue Generation` button appears to resume the stream without losing context.

---

## 📁 Project Structure

```
RedX/
├── proxy.py              # FastAPI backend — API gateway, agents, search, RAG
├── knowledge_engine.py   # ChromaDB + NetworkX knowledge storage engine
├── app.js                # Frontend logic — chat, vault, model selection
├── index.html            # UI layout and model dropdown
├── style.css             # Dark theme UI styling
├── requirements.txt      # Python dependencies
├── .env.example          # Environment template (copy to .env)
├── .gitignore            # Protects .env, logs, and local data
└── assets/               # Logo and static assets
```

---

## ⚙️ Configuration

### `.env` File
```bash
FRONTEND_URL=http://localhost:8080    # CORS origin for the frontend
BYPASS_SSL=false                      # Set to true only if behind a corporate proxy
```

### System Prompt (Sidebar)
The system prompt in the sidebar controls the AI's persona. Change it to switch roles:

| Prompt | Effect |
|---|---|
| Red Team Operator (default) | Offensive security, exploit dev, MITRE ATT&CK |
| Blue Team Analyst | Detection, SIEM, hardening, incident response |
| Bug Bounty Hunter | Responsible disclosure, CVSS scoring |
| Malware Analyst | Binary reversing, IOC extraction, YARA |

The system prompt does **not** affect the Multi-Agent Builder — each agent has its own hardcoded role.

---

## 🔐 Security Architecture

- **API Key Encryption:** AES-256-GCM, encrypted in browser `localStorage`, never on disk
- **Proxy Token Auth:** All backend requests require `X-Proxy-Token` header
- **SSRF Protection:** URL fetcher validates hostnames against private/loopback ranges
- **Data Privacy:** All knowledge stays local in `.redx_knowledge/` — nothing leaves your machine
- **No Docker, No Cloud:** Fully self-contained on your Kali host

---

## 🤖 Supported Models (Free Tier via OpenRouter)

### Top Tier (Best for Pentesting)
| Model | Context | Best For |
|---|---|---|
| `openai/gpt-oss-120b:free` | 131K | General PT, exploit dev |
| `nvidia/nemotron-3-super-120b-a12b:free` | 1M | Deep reasoning, architecture |
| `deepseek/deepseek-v4-flash:free` | 1M | OSINT, recon, huge context |
| `qwen/qwen3-coder:free` | 262K | Exploit code, shellcode |
| `poolside/laguna-m.1:free` | 131K | Payload dev, code gen |

### Fast & Lightweight
| Model | Context | Best For |
|---|---|---|
| `meta-llama/llama-3.3-70b-instruct:free` | 131K | Reliable automation |
| `nvidia/nemotron-nano-9b-v2:free` | 128K | Speed scripts |
| `minimax/minimax-m2.5:free` | 205K | Agentic tasks |

---

## 🧪 Troubleshooting

| Problem | Solution |
|---|---|
| `429 Rate Limit` errors | Normal for free tier — RedX auto-retries with backoff. Wait 60s between heavy queries. |
| Vault shows no data | Send a regular chat (not Builder) — knowledge is stored from search results. |
| `uvicorn: command not found` | Use `python3 -m uvicorn proxy:app --host 127.0.0.1 --port 3000` instead. |
| `Address already in use` | Kill existing process: `kill $(lsof -t -i:3000)` or `kill $(lsof -t -i:8080)` |
| Models returning errors | Check [OpenRouter status](https://openrouter.ai) — free models go offline occasionally. |

---

## ⚠️ Legal Disclaimer

RedX is designed **exclusively** for authorized penetration testing, red teaming, and educational purposes. Ensure you possess a valid Letter of Authorization (LoA) before utilizing the Multi-Agent Builder against any target. The developers hold no liability for misuse of this platform.

---

<div align="center">
  <b>Built for Kali Linux · Powered by OpenRouter · No Docker Required</b>
</div>
