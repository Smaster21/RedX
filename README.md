# RedX CLI 🔴

> **An elite autonomous AI agent for offensive security, development, and research — running natively on Kali Linux.**

RedX CLI is a terminal-based AI assistant that goes beyond chat. It has **eyes on your filesystem**, **hands on your shell**, and **a live connection to the internet**. Feed it a target — it will recon, exploit, document, and report.

---

## ✨ Features at a Glance

| Feature | Description |
|---|---|
| 🧠 **Multi-Model Support** | Switch between 20+ free NVIDIA NIM & OpenRouter models on-the-fly |
| 🔧 **Native Tool Calling** | AI autonomously runs `bash`, reads files, edits code, and searches the web |
| 🌐 **Live Web Search** | Proactive DuckDuckGo search — no stale training data, always current info |
| 🎯 **TACT Skills** | 36 pre-built offensive security skills (recon → exploitation → reporting) |
| 💾 **Persistent Sessions** | Every conversation is saved and resumable across reboots |
| 🧬 **Custom Personas** | Create AI personas tailored for specific tasks or projects |
| 🔒 **Safety Filters** | Hardcoded blocklist prevents destructive commands (`rm -rf /`, etc.) |
| 📎 **File Injection** | Attach any file to your prompt with `@filename` |
| ⚡ **Context Compaction** | AI-summarizes long sessions to stay within context limits |
| 🔌 **MCP Support** | Connect external tools via Model Context Protocol |

---

## ⚡ Quick Start

### 1. Prerequisites

```bash
python3 --version   # Requires Python 3.10+
pip install rich requests prompt_toolkit
```

### 2. Get API Keys

RedX works with **NVIDIA NIM** (free tier) and **OpenRouter** (free models).

```bash
# NVIDIA NIM:   https://build.nvidia.com
# OpenRouter:   https://openrouter.ai/keys

export NVIDIA_API_KEY="nvapi-xxxxxxxxxxxxxxxxxxxx"
export OPENROUTER_API_KEY="sk-or-xxxxxxxxxxxxxxxxxxxx"
```

### 3. Install RedX

```bash
git clone https://github.com/Smaster21/RedX.git
cd RedX/redx_cli

# Install as a system command
sudo cp redx.py /usr/local/bin/redx
sudo chmod +x /usr/local/bin/redx
```

### 4. Launch

```bash
redx                          # Fresh session
redx --session pentest        # Named session (saved & resumable)
redx -c                       # Continue last session
redx -y "scan localhost"      # Auto-approve all commands (lab only)
redx @app.py "find all SQLi"  # Inject a file into the prompt
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        RedX CLI (redx.py)                       │
├─────────────────────────────────────────────────────────────────┤
│  User Input (prompt_toolkit TUI)                                │
│       │                                                          │
│       ▼                                                          │
│  ┌──────────────┐   ┌────────────────────────────────────────┐  │
│  │ Slash Router  │   │          AI Loop (_ai_loop)             │  │
│  │ /model        │   │  1. build_system_prompt() [live date]   │  │
│  │ /sessions     │   │  2. sanitize_messages()                 │  │
│  │ /skill        │   │  3. stream_response() → API             │  │
│  │ /compact      │   │  4. Parse streaming tool_calls          │  │
│  │ /prompt       │   │  5. execute_native_tool()               │  │
│  │ /rename       │   │  6. Append result → loop again          │  │
│  │ /delete       │   └────────────────────────────────────────┘  │
│  └──────────────┘                                                │
├─────────────────────────────────────────────────────────────────┤
│                       Native Tool Layer                          │
│  bash            Execute shell commands (human approval gate)    │
│  web_search      DuckDuckGo live search (no key needed)          │
│  fetch_url       Fetch and strip any webpage                     │
│  read_file       Read file contents                              │
│  write_file      Create / overwrite files                        │
│  str_replace     Surgical edits with auto backup                 │
│  search_codebase ripgrep/grep code search                        │
│  list_dir        Directory tree listing                          │
│  check_job       Monitor background bash jobs                    │
├─────────────────────────────────────────────────────────────────┤
│                    Model & Provider Layer                         │
│  NVIDIA NIM  → api.nvidia.com/v1/chat/completions               │
│  OpenRouter  → openrouter.ai/api/v1/chat/completions             │
│  Heartbeat daemon polls model health every 60s                   │
│  DOWN models are hidden from /model picker automatically         │
├─────────────────────────────────────────────────────────────────┤
│                     Persistence Layer                            │
│  ~/.redx/sessions/   Session JSON files                          │
│  ~/.redx/prompts/    Custom AI personas (.md)                    │
│  ~/.redx/hooks.json  Pre/post execute shell hooks                │
│  libs/tact/skills/   36 TACT offensive security skills           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Native Tools

### `bash` — Shell Execution
Runs any shell command on your machine. Requires `y` approval per command.
```
╭─ ⚡ Execute bash? ──────────────────────╮
│  nmap -sV -sC -p- 10.10.10.1           │
╰─────────────────────────────────────────╯
Allow? [y/N]:
```

### `web_search` — Live Internet Search
DuckDuckGo-powered. The AI calls this **automatically** for any time-sensitive question — model benchmarks, CVEs, latest releases — without you needing to ask.

### `fetch_url` — Read Any Webpage
Extracts full text from any URL. Used to follow up on search results.

### `str_replace` — Surgical File Editing
Edits files with exact string matching. Creates an automatic backup. Use `/undo file <path>` to restore.

### `search_codebase` — Code Search
Uses `ripgrep` (or `grep`) to search patterns across your entire project.

---

## 🎯 TACT Skills — Security Automation

36 pre-built offensive security workflows covering the full pentest lifecycle.

```bash
/skill                              # List all 36 skills
/skill recon 10.10.10.1             # Subdomain + port scan + enum
/skill injection https://site.com   # SQLi, NoSQLi, SSTI, XXE, CMDi
/skill client-side https://site.com # XSS, CSRF, clickjacking
/skill server-side https://api.com  # SSRF, path traversal, RCE
/skill cve-poc-generator CVE-2024-1234
/skill hackthebox 10.10.11.X        # Full HTB machine automation
/tact 10.10.10.1                    # Full NIST SP 800-115 pentest
```

---

## 💬 Slash Commands Reference

### Session Management
| Command | Description |
|---|---|
| `/new [name]` | Start a fresh session |
| `/sessions` | List all saved sessions |
| `/resume <name>` | Switch to a saved session |
| `/rename <name>` | Rename current session |
| `/delete <name>` | Delete a session permanently |
| `/clear` | Wipe conversation history |
| `/undo` | Remove last exchange |
| `/compact` | AI-summarize history to free context |
| `/usage` | Token count, cost, context % |

### Model Management
| Command | Description |
|---|---|
| `/model` | Open model picker (live status) |
| `/model <name>` | Switch model by name |
| `/model next` | Rotate to next available model |

### Personas
| Command | Description |
|---|---|
| `/prompt list` | List saved personas |
| `/prompt use <name>` | Activate a persona |
| `/prompt new <name>` | Generate project-aware persona |
| `/prompt new <name> "<instructions>"` | Create from custom instructions |
| `/prompt delete <name>` | Delete a persona |

---

## 🔒 Safety

Hardcoded blocklist — **cannot be bypassed**:
- `rm -rf /`, `rm -rf ~`
- `dd if=... of=/dev/sd*`
- `mkfs.*` (disk format)
- Fork bombs
- `chmod -R 777 /`

All other commands require explicit `y` per execution.

---

## 🔑 Environment Variables

| Variable | Description |
|---|---|
| `NVIDIA_API_KEY` | NVIDIA NIM API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |

---

## 📁 File Structure

```
redx_cli/
├── redx.py          # Main CLI — single-file architecture (~4000 lines)
├── README.md        # This file
├── COMMANDS.md      # Full command reference
└── libs/
    └── tact/
        └── skills/  # 36 TACT skill definitions

~/.redx/
├── sessions/        # Saved sessions (JSON)
├── prompts/         # Custom personas (Markdown)
└── hooks.json       # Pre/post command hooks
```

---

## 🚀 Recommended Free Models

| Model | Provider | Best For |
|---|---|---|
| `nemotron-3-super-120b` | NVIDIA NIM | General reasoning, security code |
| `mistral-large-3-675b` | NVIDIA NIM | Long-form analysis |
| `llama-4-maverick-17b` | NVIDIA NIM | Fast responses |
| `gpt-oss-120b:free` | OpenRouter | Broad knowledge |

Switch anytime: `/model`

---

## 📜 License

MIT — Only test systems you own or have explicit written authorization to test.

---

*Built for security researchers, pentesters, and developers who live in the terminal.*
