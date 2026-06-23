#!/usr/bin/env python3
"""RedX CLI v2.0 — Claude Code-level Agentic Terminal Agent"""
import os, sys, json, time, re, glob, signal, argparse, subprocess, threading, atexit, uuid
from datetime import datetime
from pathlib import Path

try:
    import requests
    from rich.console import Console
    from rich.markdown import Markdown, TableElement
    from rich.table import Table
    from rich import box
    from rich.live import Live
    from rich.panel import Panel
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
except ImportError:
    print("Error: Run 'pip install rich prompt_toolkit requests'")
    sys.exit(1)

console = Console()

# ── Monkey-patch Markdown Tables ──────────────────────────────────────────────
def _table_console(self, console, options):
    t = Table(box=box.ROUNDED, show_lines=True)
    if self.header and self.header.row:
        for c in self.header.row.cells: t.add_column(c.content)
    if self.body:
        for row in self.body.rows: t.add_row(*[e.content for e in row.cells])
    yield t
TableElement.__rich_console__ = _table_console

# ── Constants ─────────────────────────────────────────────────────────────────
VERSION       = "2.0"
REDX_DIR      = Path.home() / ".redx"
SESSIONS_DIR  = REDX_DIR / "sessions"
REDX_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)
PROMPTS_DIR = REDX_DIR / "prompts"
PROMPTS_DIR.mkdir(exist_ok=True)

# Agent Skills library
# .resolve() follows the ~/.local/bin/redx symlink back to the actual file
SKILLS_ROOT       = Path(__file__).resolve().parent / "libs" / "agent"
SKILLS_DIR = SKILLS_ROOT / "skills"
SKILLS_TOOLS_DIR  = SKILLS_ROOT / "tools"

# ── Multi-Provider Config ─────────────────────────────────────────────────────
PROVIDER_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "nvidia":     "https://integrate.api.nvidia.com/v1/chat/completions",
}

# These are the OpenRouter API URLs used for model fetching
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
NVIDIA_MODELS_URL     = "https://integrate.api.nvidia.com/v1/models"

# Fallback static models used if internet is unavailable on first boot
_STATIC_OPENROUTER_MODELS = [
    "poolside/laguna-m.1:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "openai/gpt-oss-120b:free",
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-coder:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
]
_STATIC_NVIDIA_MODELS = [
    "meta/llama-3.1-405b-instruct",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/nemotron-4-340b-instruct",
    "meta/llama-3.1-8b-instruct",
]

# Models cache file — loaded from disk at startup, refreshed in background
MODELS_CACHE_FILE = REDX_DIR / "models.json"

# Live state — populated by _load_models_cache() or _fetch_models_background()
# Each entry: {"id": "model/name", "provider": "openrouter"|"nvidia", "ctx": 131072}
LIVE_MODELS: list = []
MODEL_CTX: dict   = {}  # model_id -> context window int
DEFAULT_MODELS: list = []  # flat list of model IDs (for heartbeat & session defaults)

def _models_from_cache(data: list) -> None:
    """Populate LIVE_MODELS, MODEL_CTX, DEFAULT_MODELS from a list of model dicts."""
    global LIVE_MODELS, MODEL_CTX, DEFAULT_MODELS
    LIVE_MODELS    = data
    MODEL_CTX      = {m["id"]: m.get("ctx", 131_072) for m in data}
    DEFAULT_MODELS = [m["id"] for m in data]

def _load_models_cache() -> bool:
    """Load models from disk cache. Returns True if cache exists and is valid."""
    if not MODELS_CACHE_FILE.exists():
        return False
    try:
        data = json.loads(MODELS_CACHE_FILE.read_text())
        if isinstance(data, list) and data:
            _models_from_cache(data)
            return True
    except Exception:
        pass
    return False

def _save_models_cache(data: list) -> None:
    try:
        MODELS_CACHE_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass

def _fetch_openrouter_models(or_key: str) -> list:
    """Fetch free models from OpenRouter API."""
    try:
        r = requests.get(OPENROUTER_MODELS_URL,
            headers={"Authorization": f"Bearer {or_key}"},
            timeout=15)
        if r.status_code != 200:
            return []
        models = []
        for m in r.json().get("data", []):
            pricing = m.get("pricing", {})
            is_free = (
                str(pricing.get("prompt", "1")) == "0"
                and str(pricing.get("completion", "1")) == "0"
            ) or m.get("id", "").endswith(":free")
            if not is_free:
                continue
            ctx = m.get("context_length") or 131_072
            models.append({"id": m["id"], "provider": "openrouter", "ctx": ctx,
                           "name": m.get("name", m["id"])})
        return models
    except Exception:
        return []

def _fetch_nvidia_models(nv_key: str) -> list:
    """Fetch models from NVIDIA NIM API."""
    try:
        r = requests.get(NVIDIA_MODELS_URL, timeout=15)
        if r.status_code != 200:
            return []
        models = []
        for m in r.json().get("data", []):
            mid = m.get("id", "")
            if any(x in mid.lower() for x in ["embed", "parse", "detector", "calibration", "gliner-pii", "reward", "translate"]):
                continue
            models.append({"id": mid, "provider": "nvidia",
                           "ctx": 131_072, "name": m.get("id", "")})
        return models
    except Exception:
        return []

def _refresh_models(or_key: str, nv_key: str, silent: bool = True) -> None:
    """Fetch fresh models from both providers and update cache."""
    global LIVE_MODELS, MODEL_CTX, DEFAULT_MODELS
    or_models = _fetch_openrouter_models(or_key)
    nv_models = _fetch_nvidia_models(nv_key)
    
    if not or_models:
        or_models = [{"id": m, "provider": "openrouter", "ctx": 131_072, "name": m} for m in _STATIC_OPENROUTER_MODELS]
    if not nv_models:
        nv_models = [{"id": m, "provider": "nvidia", "ctx": 131_072, "name": m} for m in _STATIC_NVIDIA_MODELS]
        
    all_models = or_models + nv_models

    _models_from_cache(all_models)
    _save_models_cache(all_models)
    if not silent:
        or_count = len([m for m in all_models if m["provider"] == "openrouter"])
        nv_count  = len([m for m in all_models if m["provider"] == "nvidia"])
        console.print(f"[dim green]🔄 Models refreshed: {or_count} OpenRouter, {nv_count} NVIDIA[/dim green]")

def get_model_provider(model_id: str) -> str:
    """Return the provider for a given model id."""
    for m in LIVE_MODELS:
        if m["id"] == model_id:
            return m["provider"]
    # Heuristic fallback
    return "openrouter"

def get_api_url(model_id: str) -> str:
    return PROVIDER_ENDPOINTS.get(get_model_provider(model_id), PROVIDER_ENDPOINTS["openrouter"])

def get_api_key_for(model_id: str, or_key: str, nv_key: str = "") -> str:
    provider = get_model_provider(model_id)
    if provider == "nvidia":
        key = nv_key or os.environ.get("NVIDIA_API_KEY", "")
        if not key:
            console.print("[yellow]⚠ NVIDIA model selected but NVIDIA_API_KEY is not set.\n"
                          "Set it with: export NVIDIA_API_KEY=nvapi-...[/yellow]")
        return key
    return or_key or os.environ.get("OPENROUTER_API_KEY", "")

# ── Heartbeat Monitor ─────────────────────────────────────────────────────────
from concurrent.futures import ThreadPoolExecutor, as_completed

MODEL_STATUS      = {}  # UP/LIMIT/DOWN/UNKNOWN — populated after model fetch
MODEL_LAST_CHECK  = {}   # {model: datetime}
HEARTBEAT_CYCLE   = [None, None]
MODEL_STATUS_LOCK = threading.Lock()
HEARTBEAT_INTERVAL = 60

def _ago(dt) -> str:
    if dt is None: return "never"
    secs = int((datetime.now() - dt).total_seconds())
    if secs < 5:    return "just now"
    if secs < 60:   return f"{secs}s ago"
    if secs < 3600: return f"{secs//60}m {secs%60:02d}s ago"
    return f"{secs//3600}h ago"

def ping_model(model: str, or_key: str, nv_key: str = "") -> str:
    """Lightweight 1-token ping routed to the correct provider."""
    try:
        url = get_api_url(model)
        key = get_api_key_for(model, or_key, nv_key)
        if not key:
            return "DOWN"
        r = requests.post(url,
            json={"model": model,
                  "messages": [{"role": "user", "content": "hi"}],
                  "max_tokens": 1, "stream": False},
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            timeout=8)
        if r.status_code == 200:  return "UP"
        if r.status_code == 429:  return "LIMIT"
        return "DOWN"
    except Exception:
        return "DOWN"

def run_heartbeat(or_key: str, nv_key: str = ""):
    """Background daemon: refreshes model list then pings all models every 60s."""
    # First run: refresh the model list silently
    _refresh_models(or_key, nv_key, silent=True)
    while True:
        cycle_start = datetime.now()
        with MODEL_STATUS_LOCK:
            HEARTBEAT_CYCLE[0] = cycle_start
            HEARTBEAT_CYCLE[1] = None
        models_to_ping = [m["id"] for m in LIVE_MODELS]
        with ThreadPoolExecutor(max_workers=30) as ex:
            futures = {ex.submit(ping_model, m, or_key, nv_key): m for m in models_to_ping}
            for f in as_completed(futures):
                m = futures[f]
                result = f.result()
                with MODEL_STATUS_LOCK:
                    MODEL_STATUS[m]     = result
                    MODEL_LAST_CHECK[m] = datetime.now()
        with MODEL_STATUS_LOCK:
            HEARTBEAT_CYCLE[1] = datetime.now()
        time.sleep(HEARTBEAT_INTERVAL)

def status_icon(model: str) -> str:
    s = MODEL_STATUS.get(model, "UNKNOWN")
    return {"UP": "[green]🟢[/green]", "LIMIT": "[yellow]🟡[/yellow]",
            "DOWN": "[red]🔴[/red]", "UNKNOWN": "[dim]⬜[/dim]"}[s]

def build_system_prompt() -> str:
    """Build the system prompt with the real current date injected at runtime."""
    now = datetime.now()
    date_str = now.strftime("%A, %B %d, %Y")      # e.g. Thursday, June 12, 2026
    time_str = now.strftime("%H:%M %Z").strip()    # e.g. 07:22
    return f"""You are RedX CLI, an elite autonomous hacking and development agent running on Kali Linux.
You have full access to the user's machine via native tools (bash, read_file, str_replace, search_codebase, web_search).

== DATE & TIME (CRITICAL) ==
Today's date is: {date_str}
Current time is: {time_str}
ALWAYS use this date when writing to files, logs, reports, or task updates.
NEVER guess or use a date from training memory. If unsure of the date, run: `date` via bash.

== LIVE INFORMATION POLICY (MANDATORY — READ CAREFULLY) ==
Your training data has a cutoff date. The current year is {now.year}. Many things have changed.

YOU MUST call `web_search` PROACTIVELY (without being asked) whenever the user asks about:
  - Any AI model, tool, software, or framework (versions, benchmarks, comparisons, releases)
  - CVEs, vulnerabilities, exploits, or security advisories
  - Current events, news, or anything happening in the world
  - Prices, availability, or product specifications
  - People, organizations, or companies and their current status
  - Anything that could have changed since your training cutoff

DO NOT answer from memory alone for these topics. Your memorized data is STALE and OUTDATED.
ALWAYS search first, then synthesize the live results into your answer.

The ONLY time you may skip web_search is when the user explicitly asks about:
  - Historical facts (events before 2020)
  - Stable technical concepts (algorithms, math, protocols that do not change)
  - Something from the current session context

If you are even slightly unsure whether information might be outdated → SEARCH FIRST.

== TOOL USE RULES ==
- Use `web_search` for any real-time, current, or potentially outdated information.
- Use `fetch_url` to read the full content of a specific URL found in search results.
- Use `bash` to run shell commands.
- Use `read_file` to inspect files without executing them.
- Use `str_replace` for precise file edits.
- Use `search_codebase` to find patterns in code.
- For casual conversation or stable knowledge → reply in plain text, no tools needed.

== FORMATTING RULES (CRITICAL) ==
1. When drawing flow diagrams or step-by-step processes, use a MARKDOWN TABLE instead of ASCII box art.
   Good example:
   | Step | Description |
   |------|-------------|
   | 1. Prompt Ingestion | User prompt is received |
   | 2. Scope Determination | git diff, file-list, ignore rules |

2. NEVER draw boxes with text descriptions hanging OUTSIDE the box border like this (BAD):
   ┌─────────────────┐
   │ Step Name       │    (description outside box — BAD)
   └─────────────────┘
   Instead, put ALL content inside the table cell or inside the box.

3. For simple lists, use standard markdown bullet points (- item).
4. For code, always use fenced code blocks with a language tag (```python, ```bash, etc.).
5. Keep responses concise. Avoid unnecessary padding or decorative separators.
"""

SYSTEM_PROMPT = build_system_prompt()


DANGEROUS = [r"rm\s+-rf\s+/", r"dd\s+if=", r"mkfs\.", r":\(\)\{.*\}",
             r"chmod\s+-R\s+777\s+/", r">\s+/dev/sda", r"sudo\s+rm\s+-rf\s+/"]

# ── Native Tools & MCP ────────────────────────────────────────────────────────
import uuid
import threading
import json
import subprocess
import shlex

BACKGROUND_JOBS = {}
ACTIVE_PIDS_FILE = REDX_DIR / "active_pids.json"

def get_sys_ram_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except: pass
    return 2048  # fallback

def track_job(job_id, pgid, cmd):
    jobs = {}
    if ACTIVE_PIDS_FILE.exists():
        try: jobs = json.loads(ACTIVE_PIDS_FILE.read_text())
        except: pass
    jobs[job_id] = {"pgid": pgid, "cmd": cmd, "started": time.time()}
    ACTIVE_PIDS_FILE.write_text(json.dumps(jobs))

def untrack_job(job_id):
    if ACTIVE_PIDS_FILE.exists():
        try:
            jobs = json.loads(ACTIVE_PIDS_FILE.read_text())
            if job_id in jobs:
                del jobs[job_id]
                ACTIVE_PIDS_FILE.write_text(json.dumps(jobs))
        except: pass

def kill_job_group(pgid):
    try:
        os.killpg(pgid, signal.SIGTERM)
        time.sleep(0.5)
        os.killpg(pgid, signal.SIGKILL)
    except: pass

def cleanup_all_jobs():
    if ACTIVE_PIDS_FILE.exists():
        try:
            jobs = json.loads(ACTIVE_PIDS_FILE.read_text())
            for j in jobs.values():
                kill_job_group(j["pgid"])
            ACTIVE_PIDS_FILE.unlink()
        except: pass

atexit.register(cleanup_all_jobs)
FILE_BACKUPS = {}

import os
os.makedirs('/tmp/redx_backups', exist_ok=True)

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch text content from a URL (strips HTML).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": "Spawn a background AI sub-agent for parallel tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed instructions for the sub-agent."}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command on the local Kali Linux system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to run."},
                    "background": {"type": "boolean", "description": "Run in background, returning job ID immediately."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": "Precision string replacement in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                    "old_str": {"type": "string", "description": "Exact string to replace."},
                    "new_str": {"type": "string", "description": "String to replace with."}
                },
                "required": ["path", "old_str", "new_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": "Search the codebase using ripgrep/grep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Regex or string to search for."},
                    "path": {"type": "string", "description": "Directory or file to search in. Default is '.'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content. Safer than bash echo for long/special content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to write."},
                    "content": {"type": "string", "description": "Full file content to write."},
                    "mode": {"type": "string", "description": "'overwrite' (default) or 'append'."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents as structured data (name, type, size, modified).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list. Default is '.'"},
                    "recursive": {"type": "boolean", "description": "If true, recurse into subdirectories (max 2 levels)."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_job",
            "description": "Check the status and output of a background bash job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The ID of the background job."}
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for real-time, up-to-date information. Use this whenever the user asks about current events, latest model releases, recent CVEs, live pricing, news, or anything that may have changed after your training cutoff. Returns a structured list of search results with titles, snippets, and URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query to look up."},
                    "max_results": {"type": "integer", "description": "Maximum number of results to return (default 8, max 20)."}
                },
                "required": ["query"]
            }
        }
    }
]

class MCPClient:
    """Minimalist synchronous MCP over Stdio Client."""
    def __init__(self, command: str):
        self.command = command
        self.p = None
        self._id = 1
        self._lock = threading.Lock()
        
    def start(self):
        args = shlex.split(self.command)
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        res = self.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "RedX CLI", "version": "2.0"}
        })
        self.send_notification("initialized", {})
        return res

    def send_notification(self, method: str, params: dict):
        if not self.p: return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        with self._lock:
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
            
    def send_request(self, method: str, params: dict) -> dict:
        if not self.p: return {}
        req_id = self._id
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        
        with self._lock:
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
            while True:
                line = self.p.stdout.readline()
                if not line: return {}
                try:
                    resp = json.loads(line)
                    if resp.get("id") == req_id:
                        if "error" in resp: raise Exception(resp["error"])
                        return resp.get("result", {})
                except:
                    pass

    def get_tools(self):
        res = self.send_request("tools/list", {})
        return res.get("tools", [])
        
    def call_tool(self, name: str, args: dict):
        res = self.send_request("tools/call", {"name": name, "arguments": args})
        out = ""
        for c in res.get("content", []):
            if c.get("type") == "text": out += c.get("text", "")
            else: out += str(c)
        return out if out else "Success"

MCP_CLIENTS = {}
MCP_TOOLS_CACHE = []

def load_mcp_servers():
    global MCP_TOOLS_CACHE
    config_path = REDX_DIR / "mcp_servers.json"
    if not config_path.exists():
        config_path.write_text(json.dumps({"mcpServers": {}}))
        return
        
    try:
        config = json.loads(config_path.read_text())
        servers = config.get("mcpServers", {})
        for name, opts in servers.items():
            if name not in MCP_CLIENTS:
                cmd = opts.get("command", "")
                args = opts.get("args", [])
                full_cmd = f"{cmd} {' '.join(args)}"
                console.print(f"[dim]🔌 Connecting MCP: {name}...[/dim]")
                try:
                    client = MCPClient(full_cmd)
                    client.start()
                    MCP_CLIENTS[name] = client
                    tools = client.get_tools()
                    for t in tools:
                        MCP_TOOLS_CACHE.append({
                            "type": "function",
                            "function": {
                                "name": f"mcp_{name}__{t['name']}",
                                "description": t.get("description", ""),
                                "parameters": t.get("inputSchema", {})
                            }
                        })
                except Exception as e:
                    console.print(f"[red]❌ MCP {name} failed: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Error loading MCP: {e}[/red]")

def execute_native_tool(tool_name: str, args: dict, hooks: dict, auto_yes: list, session) -> str:
    """Dispatcher for all native tools."""
    if tool_name.startswith("mcp_"):
        parts = tool_name[4:].split("__", 1)
        if len(parts) == 2:
            server_name, original_tool = parts
            client = MCP_CLIENTS.get(server_name)
            if client:
                try:
                    console.print(f"[dim cyan]🔌 MCP {server_name}: calling {original_tool}...[/dim cyan]")
                    return client.call_tool(original_tool, args)
                except Exception as e:
                    return f"MCP Error: {e}"
        return f"Error: MCP Tool {tool_name} not found or server offline."

    if tool_name == "bash":
        cmd = args.get("command", "")
        bg = args.get("background", False)
        if not cmd: return "Error: empty command"
        
        ok, hook_out = run_hook(hooks, "pre_execute", cmd)
        if not ok: return f"Hook blocked command: {hook_out}"
        
        if is_dangerous(cmd):
            console.print(Panel(f"[bold red]🚨 BLOCKED (destructive pattern)[/bold red]\n[yellow]{cmd}[/yellow]", border_style="red", title="Safety"))
            return "Error: Command blocked by safety rules."

        # Resource Pre-Check for Heavy Tools
        heavy_tools = ["zaproxy", "nuclei", "nikto", "nmap -A", "gobuster", "ffuf", "metasploit"]
        if any(h in cmd.lower() for h in heavy_tools):
            ram_mb = get_sys_ram_mb()
            if ram_mb < 600:
                console.print(Panel(f"[bold red]🚨 RESOURCE BLOCKED[/bold red]\nCommand '{cmd}' requires more RAM. Only {ram_mb}MB available.", border_style="red"))
                return f"Error: System RAM critically low ({ram_mb}MB). Cannot launch heavy tool. Find an alternative or free up memory."

        if auto_yes[0]:
            console.print(Panel(cmd, title="⚡ [green]PERMIT MODE[/green] — auto-running", border_style="green"))
            allow = "y"
        else:
            console.print(Panel(cmd, title="⚡ Execute bash?", border_style="yellow"))
            try:
                from prompt_toolkit import prompt as pp
                allow = pp(HTML("<b><ansiyellow>Allow? [y/N]: </ansiyellow></b>"))
            except:
                allow = input("Allow? [y/N]: ")

        if allow.lower() != "y":
            console.print("[red]Denied.[/red]")
            return "Error: User denied execution."

        # Ulimit Wrapper to prevent OOM
        safe_cmd = f"ulimit -v 4194304 2>/dev/null; {cmd}"
            
        import os as _os
        if bg:
            job_id = str(uuid.uuid4())[:8]
            console.print(f"[dim yellow]Started job {job_id} in background...[/dim yellow]")
            p = subprocess.Popen(safe_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=_os.setpgrp)
            BACKGROUND_JOBS[job_id] = p
            track_job(job_id, p.pid, cmd)
            return f"Job {job_id} started in background. Use check_job to monitor."
        
        import time
        t0 = time.time()
        with console.status("[bold yellow]Running...[/bold yellow]"):
            try:
                # Use preexec_fn so we can cleanly kill it if needed later
                r = subprocess.run(safe_cmd, shell=True, capture_output=True, text=True, timeout=120, preexec_fn=_os.setpgrp)
                out = r.stdout + r.stderr
                elapsed = time.time() - t0
                code = r.returncode
                badge = f"[green]✅ Exit 0[/green]" if code == 0 else f"[red]❌ Exit {code}[/red]"
                if not out.strip():
                    out = "[no output]" if code == 0 else f"[failed with exit code {code}]"
            except subprocess.TimeoutExpired:
                out = "[TIMEOUT after 120s]"; badge = "[red]❌ Timeout[/red]"; elapsed = 120
            except Exception as e:
                out = f"[ERROR: {e}]"; badge = "[red]❌ Error[/red]"; elapsed = 0
                
        console.print(f"{badge} [dim]({elapsed:.1f}s)[/dim]")
        if len(out) > 4000:
            out = out[:2000] + "\n\n... [TRUNCATED] ...\n\n" + out[-2000:]
        console.print(Panel(out[:3000], border_style="dim", title="Output"))
        run_hook(hooks, "post_execute", cmd)
        return out
        
    elif tool_name == "read_file":
        path = args.get("path", "")
        try:
            with open(path, "r") as f:
                c = f.read()
                return c[:8000] + ("...[TRUNCATED]" if len(c) > 8000 else "")
        except Exception as e:
            return str(e)
            
    elif tool_name == "str_replace":
        path = args.get("path", "")
        old_str = args.get("old_str", "")
        new_str = args.get("new_str", "")
        try:
            with open(path, "r") as f: content = f.read()
            if content.count(old_str) != 1:
                return f"Error: old_str found {content.count(old_str)} times. Must be exactly 1."
            
            # Backup for undo
            import shutil, time
            backup_path = f"/tmp/redx_backups/{Path(path).name}_{int(time.time())}.bak"
            shutil.copy2(path, backup_path)
            FILE_BACKUPS[path] = backup_path
            
            content = content.replace(old_str, new_str)
            with open(path, "w") as f: f.write(content)
            
            # Show mini diff
            import difflib
            diff = list(difflib.unified_diff(old_str.splitlines(), new_str.splitlines(), lineterm=""))
            diff_text = "\n".join(diff[:10])
            if len(diff) > 10: diff_text += "\n... [TRUNCATED]"
            console.print(Panel(diff_text, title=f"⚡ Patched {path}", border_style="green"))
            
            return f"Successfully replaced string in {path}. Backup saved to {backup_path}"
        except Exception as e:
            return str(e)
            
    elif tool_name == "search_codebase":
        query = args.get("query", "")
        path = args.get("path", ".")
        try:
            import shutil
            if shutil.which("rg"):
                cmd = ["rg", "-n", "--no-heading", "--color=never", query, path]
            else:
                cmd = ["grep", "-rnE", query, path]
                
            r = subprocess.run(cmd, capture_output=True, text=True)
            out = r.stdout + r.stderr
            if len(out) > 8000: out = out[:8000] + "\n...[TRUNCATED]"
            return out or "No matches found."
        except Exception as e:
            return str(e)
            
    elif tool_name == "fetch_url":
        url = args.get("url", "")
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            try:
                from bs4 import BeautifulSoup
                text = BeautifulSoup(r.text, "html.parser").get_text(separator="\n", strip=True)
            except ImportError:
                # Fallback if bs4 not installed
                text = re.sub('<[^<]+>', '\n', r.text)
            
            if len(text) > 8000: text = text[:8000] + "... [TRUNCATED]"
            return text
        except Exception as e:
            return f"Error fetching {url}: {e}"

    elif tool_name == "spawn_subagent":
        prompt = args.get("prompt", "")
        import uuid, threading
        job_id = f"subagent_{str(uuid.uuid4())[:8]}"
        
        def run_subagent():
            # In a real implementation this would spawn a new session and run the agent loop
            # For this MVP, we simulate a background AI task using bash echo + sleep for proof of concept
            # because RedX CLI is fundamentally single-threaded in its current architecture.
            pass
            
        return f"Feature disabled: RedX requires architectural refactoring to support true parallel sub-agents via python threading."

    elif tool_name == "write_file":
        path = args.get("path", "")
        content_str = args.get("content", "")
        mode = args.get("mode", "overwrite")
        try:
            import shutil, time
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            # Backup if file exists
            if p.exists():
                backup = f"/tmp/redx_backups/{p.name}_{int(time.time())}.bak"
                shutil.copy2(p, backup)
                FILE_BACKUPS[path] = backup
            write_mode = "a" if mode == "append" else "w"
            with open(path, write_mode, encoding="utf-8") as f:
                f.write(content_str)
            size = p.stat().st_size
            console.print(f"[green]✅ Written {path} ({size:,} bytes)[/green]")
            return f"Successfully wrote {size} bytes to {path}."
        except Exception as e:
            return f"Error writing {path}: {e}. SUGGESTION: Check path exists and you have write permission."

    elif tool_name == "list_dir":
        dirpath = args.get("path", ".")
        recursive = args.get("recursive", False)
        try:
            import os as _os
            entries = []
            base = Path(dirpath)
            if not base.exists():
                return f"Error: {dirpath} does not exist."
            def scan(p: Path, depth: int = 0):
                try:
                    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
                except PermissionError:
                    return
                for item in items:
                    rel = str(item.relative_to(base))
                    stat = item.stat()
                    kind = "dir" if item.is_dir() else "file"
                    sz = "" if item.is_dir() else f"{stat.st_size:,}B"
                    entries.append(f"{'  ' * depth}{'📁' if item.is_dir() else '📄'} {item.name:<40} {kind:<5} {sz}")
                    if recursive and item.is_dir() and depth < 2:
                        scan(item, depth + 1)
            scan(base)
            result = "\n".join(entries) if entries else "(empty)"
            return f"Directory: {dirpath}\n\n{result}"
        except Exception as e:
            return f"Error listing {dirpath}: {e}"

    elif tool_name == "check_job":
        job_id = args.get("job_id", "")
        p = BACKGROUND_JOBS.get(job_id)
        if not p: return f"Error: Job {job_id} not found."
        import fcntl, os
        fd = p.stdout.fileno()
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        try:
            out = p.stdout.read()
        except TypeError:
            out = ""
        except Exception:
            out = ""
            
        code = p.poll()
        if code is not None:
            untrack_job(job_id)
            del BACKGROUND_JOBS[job_id]
        status = "Running" if code is None else f"Exited {code}"
        return f"Job {job_id} Status: {status}\nOutput:\n{out}"
        
    elif tool_name == "web_search":
        query = args.get("query", "")
        max_results = min(int(args.get("max_results", 8)), 20)
        if not query:
            return "Error: No query provided."
        try:
            console.print(f"[dim cyan]🔍 Searching web: {query}[/dim cyan]")
            # Use DuckDuckGo Instant Answer API (free, no key)
            ddg_url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
                "no_redirect": "1",
            }
            r = requests.get(ddg_url, params=params, timeout=15,
                             headers={"User-Agent": "RedX CLI/2.0 (research tool)"})
            r.raise_for_status()
            data = r.json()

            results = []
            # Abstract (featured answer)
            if data.get("Abstract"):
                results.append({
                    "title": data.get("Heading", "Featured Answer"),
                    "snippet": data["Abstract"],
                    "url": data.get("AbstractURL", "")
                })
            # Related topics
            for topic in data.get("RelatedTopics", []):
                if isinstance(topic, dict) and topic.get("Text") and topic.get("FirstURL"):
                    results.append({
                        "title": topic.get("Text", "")[:80],
                        "snippet": topic.get("Text", ""),
                        "url": topic.get("FirstURL", "")
                    })
                    if len(results) >= max_results:
                        break

            # If DDG Instant Answers has no results, fallback to DDG HTML search
            if not results:
                html_url = "https://html.duckduckgo.com/html/"
                r2 = requests.post(html_url, data={"q": query}, timeout=15,
                                   headers={"User-Agent": "Mozilla/5.0 (compatible; RedX/2.0)"})
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r2.text, "html.parser")
                    for a in soup.select("a.result__a")[:max_results]:
                        snippet_el = a.find_parent("div", class_="result")
                        snippet = ""
                        if snippet_el:
                            snip_tag = snippet_el.find("a", class_="result__snippet")
                            if snip_tag:
                                snippet = snip_tag.get_text(strip=True)
                        results.append({
                            "title": a.get_text(strip=True),
                            "snippet": snippet,
                            "url": a.get("href", "")
                        })
                except ImportError:
                    # No bs4 — simple regex extraction
                    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r2.text)
                    for t in titles[:max_results]:
                        results.append({"title": re.sub('<[^>]+>', '', t), "snippet": "", "url": ""})

            if not results:
                return f"No results found for: {query}"

            out = f"🔍 Web Search Results for: '{query}'\n" + "─" * 60 + "\n"
            for i, res in enumerate(results[:max_results], 1):
                out += f"\n[{i}] {res['title']}\n"
                if res['snippet']:
                    out += f"    {res['snippet'][:200]}\n"
                if res['url']:
                    out += f"    🔗 {res['url']}\n"
            out += "─" * 60
            console.print(f"[dim green]✅ Found {len(results[:max_results])} results[/dim green]")
            return out
        except Exception as e:
            return f"Web search failed: {e}. Try fetch_url with a specific URL instead."

    return f"Error: Unknown tool {tool_name}"


_cancel = threading.Event()

# ── API Key ───────────────────────────────────────────────────────────────────
def load_api_keys():
    or_key = os.environ.get("OPENROUTER_API_KEY", "")
    nv_key = os.environ.get("NVIDIA_API_KEY", "")
    
    for p in [Path("/home/kali/Desktop/OPENROUTE/redx_chatbot/.env"),
              REDX_DIR / ".env", Path(".env")]:
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY=") and not or_key:
                    or_key = line.split("=",1)[1].strip().strip('"\'')
                elif line.startswith("NVIDIA_API_KEY=") and not nv_key:
                    nv_key = line.split("=",1)[1].strip().strip('"\'')
    if or_key: os.environ["OPENROUTER_API_KEY"] = or_key
    if nv_key: os.environ["NVIDIA_API_KEY"] = nv_key
    return or_key, nv_key

# ── Session ───────────────────────────────────────────────────────────────────
class Session:
    def __init__(self, name="default"):
        self.name    = name
        self.path    = SESSIONS_DIR / f"{name}.json"
        self.messages= []
        self.tokens  = 0
        self.cost    = 0.0
        self.models  = list(DEFAULT_MODELS)
        self.prompt  = "default"
        self.created = datetime.now().isoformat()
        self.data    = {}   # persistent key-value store (e.g. target, scope)

    def load(self):
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text())
                msgs = d.get("messages", [])
                # Sanitize old tool_calls: strip streaming-only 'index' field
                for m in msgs:
                    if m.get("role") == "assistant" and "tool_calls" in m:
                        for tc in m["tool_calls"]:
                            tc.pop("index", None)
                self.messages = msgs
                self.tokens   = d.get("tokens", 0)
                self.cost     = d.get("cost", 0.0)
                self.prompt   = d.get("prompt", "default")
                self.created  = d.get("created", self.created)
                self.data     = d.get("data", {})
                # Always use the latest DEFAULT_MODELS; keep any user-added
                # custom models that aren't in the current default list
                saved = d.get("models", [])
                custom = [m for m in saved if m not in DEFAULT_MODELS]
                self.models = custom + list(DEFAULT_MODELS)
                return True
            except: pass
        return False

    def save(self):
        self.path.write_text(json.dumps({
            "name": self.name, "messages": self.messages,
            "tokens": self.tokens, "cost": self.cost,
            "models": self.models, "prompt": self.prompt,
            "created": self.created, "updated": datetime.now().isoformat(),
            "data": self.data,
        }, indent=2))

    def reset(self):
        self.messages = [{"role":"system","content":SYSTEM_PROMPT}]
        self.tokens = 0; self.cost = 0.0
        self.save()

    @property
    def ctx_pct(self):
        est = sum(len(m.get("content","")) for m in self.messages) // 4
        ctx = MODEL_CTX.get(self.models[0] if self.models else "", 128000)
        return min(100, int(est / ctx * 100))

    @property
    def user_count(self):
        return sum(1 for m in self.messages if m["role"]=="user")

def list_sessions():
    out = []
    for p in sorted(SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text())
            models = d.get("models", [])
            active_model = models[0].split("/")[-1] if models else "?"
            size_kb = p.stat().st_size // 1024
            out.append({"name": p.stem,
                        "created": d.get("created","?")[:10],
                        "updated": d.get("updated","?")[:16].replace("T"," "),
                        "msgs": sum(1 for m in d.get("messages",[]) if m["role"]=="user"),
                        "tokens": d.get("tokens",0),
                        "model": active_model,
                        "size_kb": size_kb})
        except: pass
    return out

# ── Persona / Prompt Management ───────────────────────────────────────────────
def ensure_default_prompt():
    default_p = PROMPTS_DIR / "default.md"
    if default_p.exists(): return
    default_p.write_text(
"""# RedX Default Persona

## Who I Am
- I am a security researcher and developer.
- I work primarily on Kali Linux.

## Preferred Tools
- Recon: nmap, amass, subfinder
- Web: Burp Suite, ffuf, nuclei
- Exploitation: Metasploit, sqlmap

## Rules
- Always explain what you are doing before running commands
- Never delete files without confirmation
- Prefer targeted over noisy scans
"""
    )
    console.print(f"[dim green]✨ Created default prompt: {default_p}[/dim green]")

def get_available_prompts():
    return sorted([p.stem for p in PROMPTS_DIR.glob("*.md")])

def load_active_prompt(prompt_name):
    p = PROMPTS_DIR / f"{prompt_name}.md"
    if p.exists():
        return p.read_text(errors="replace").strip()
    return ""

# ── Hooks ─────────────────────────────────────────────────────────────────────
def load_hooks():
    for p in [Path(".redx/hooks.json"), REDX_DIR/"hooks.json"]:
        if p.exists():
            try: return json.loads(p.read_text())
            except: pass
    return {}

def run_hook(hooks, event, cmd=""):
    script = hooks.get(event)
    if not script: return True, ""
    try:
        r = subprocess.run(script, shell=True, capture_output=True, text=True,
                           timeout=10, env={**os.environ,"REDX_CMD":cmd,"REDX_EVENT":event})
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e: return True, str(e)

# ── Safety ────────────────────────────────────────────────────────────────────
def is_dangerous(cmd):
    return any(re.search(p, cmd, re.IGNORECASE) for p in DANGEROUS)

# ── Skill Helpers ────────────────────────────────────────────────────────
def list_tact_skills() -> list:
    """Return [{name, description}] for all available skills."""
    skills = []
    if not SKILLS_DIR.exists():
        return skills
    for d in sorted(SKILLS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            md = d / "SKILL.md"
            if md.exists():
                first = md.read_text(errors="replace").split("\n")[0].strip("# -")[:75]
                skills.append({"name": d.name, "description": first})
    return skills

def load_tact_skill(skill_name: str) -> str | None:
    """
    Load a SKILL.md by name (supports partial/prefix matching).
    Also loads up to 3 reference/*.md files if present.
    Returns the full content string, or None if not found.
    """
    if not SKILLS_DIR.exists():
        return None
    match = None
    for d in SKILLS_DIR.iterdir():
        if not d.is_dir(): continue
        if d.name == skill_name or d.name.startswith(skill_name):
            md = d / "SKILL.md"
            if md.exists():
                match = (d, md)
                break
    if not match:
        return None
    skill_dir, skill_md = match
    content = skill_md.read_text(errors="replace")
    # Append reference files (payload lists, cheat sheets, role prompts)
    ref_dir = skill_dir / "reference"
    if ref_dir.exists():
        for rf in sorted(ref_dir.glob("*.md"))[:4]:
            snippet = rf.read_text(errors="replace")[:4000]
            content += f"\n\n---\n### Reference: {rf.name}\n{snippet}"
    return content

# ── Streaming ─────────────────────────────────────────────────────────────────
def stream_response(session: Session, api_key: str, stop_spinner=None, nv_key: str = ""):
    """Try ONLY the active model (session.models[0]). No silent fallback."""
    _cancel.clear()
    if not session.models:
        console.print("[red]No model selected. Run /model to pick one.[/red]")
        return None, {}, None

    model = session.models[0]
    try:
        # Trim history to fit this model's context window
        ctx_limit = MODEL_CTX.get(model, 128_000)
        msgs = session.messages[:]
        if len(msgs) > 1:
            max_chars = (ctx_limit - 6000) * 4
            while len(msgs) > 2:
                total_chars = sum(len(str(m.get("content") or "")) for m in msgs)
                if total_chars <= max_chars:
                    break
                msgs.pop(1)

        # Sanitize any corrupted tool_calls in history (arguments=None causes HTTP 400)
        sanitized = []
        skip_tool_ids = set()
        for m in msgs:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                clean_tcs = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    if fn.get("arguments") is None or fn.get("arguments") == "":
                        skip_tool_ids.add(tc.get("id", ""))
                        tc = {**tc, "function": {**fn, "arguments": "{}"}}
                    clean_tcs.append(tc)
                m = {**m, "tool_calls": clean_tcs}
            # Also drop orphaned tool result messages whose call was stripped
            if m.get("role") == "tool" and m.get("tool_call_id") in skip_tool_ids:
                continue
            sanitized.append(m)
        msgs = sanitized

        # Always inject the latest system prompt (overrides stale cached prompt in session files)
        fresh_system = build_system_prompt()
        if msgs and msgs[0].get("role") == "system":
            msgs[0] = {"role": "system", "content": fresh_system}
        else:
            msgs.insert(0, {"role": "system", "content": fresh_system})

        # Route to correct provider
        provider = get_model_provider(model)
        api_url  = get_api_url(model)
        req_key  = get_api_key_for(model, api_key, nv_key)
        headers  = {"Authorization": f"Bearer {req_key}",
                    "Content-Type": "application/json"}
        if provider == "openrouter":
            headers["X-Title"] = "RedX CLI"

        payload = {
            "model": model,
            "messages": msgs,
            "temperature": 0.2,
            "max_tokens": 4096,
            "stream": True,
            "tools": AGENT_TOOLS + MCP_TOOLS_CACHE
        }
        
        
        with open("debug_payload.json", "w") as f: json.dump(payload, f)
        r = requests.post(api_url, json=payload,
            headers=headers, timeout=120, stream=True)

        if r.status_code == 429:
            with MODEL_STATUS_LOCK:
                MODEL_STATUS[model] = "LIMIT"
            if stop_spinner is not None: stop_spinner.set()
            console.print(Panel(
                f"[yellow]🟡 [bold]{model.split('/')[-1]}[/bold] is rate-limited (429).[/yellow]\n"
                "Run [bold]/model next[/bold] to switch to the next available model,\n"
                "or [bold]/model[/bold] to see all models and their status.",
                title="Rate Limited", border_style="yellow"))
            return None, {}, None

        if r.status_code == 503:
            with MODEL_STATUS_LOCK:
                MODEL_STATUS[model] = "DOWN"
            if stop_spinner is not None: stop_spinner.set()
            console.print(Panel(
                f"[red]🔴 [bold]{model.split('/')[-1]}[/bold] is unavailable (503).[/red]\n"
                "Run [bold]/model next[/bold] to switch to another model.",
                title="Model Down", border_style="red"))
            return None, {}, None

        if r.status_code != 200:
            with MODEL_STATUS_LOCK:
                MODEL_STATUS[model] = "DOWN"
            if stop_spinner is not None: stop_spinner.set()
            err_msg = ""
            try: err_msg = r.json().get("error", {}).get("message", r.text[:250])
            except: err_msg = r.text[:250]
            console.print(Panel(
                f"[red]HTTP {r.status_code} from [bold]{model.split('/')[-1]}[/bold].[/red]\n\n"
                f"[dim]Message:[/dim] {err_msg}\n\n"
                "Run [bold]/compact[/bold] if session is very large, then retry.\n"
                "Or run [bold]/model next[/bold] to switch models.",
                title="Request Failed", border_style="red"))
            return None, {}, None

        full, usage, buf = "", {}, ""
        tool_calls_dict = {}
        first_token = True

        for raw in r.iter_lines():
            if _cancel.is_set(): return None, {}, None
            if not raw: continue
            line = raw.decode() if isinstance(raw, bytes) else raw
            if not line.startswith("data:"): continue
            data = line[5:].strip()
            if data == "[DONE]": break
            try:
                chunk = json.loads(data)
                delta = chunk.get("choices", [{}])[0].get("delta", {})

                tok = delta.get("content", "")
                if tok:
                    if first_token:
                        if stop_spinner is not None:
                            stop_spinner.set()
                            time.sleep(0.05)
                        from rich.live import Live
                        from rich.markdown import Markdown
                        console.print(f"\n[bold magenta]🤖 RedX:[/bold magenta]")
                        live_md = Live(console=console, refresh_per_second=15, transient=False)
                        live_md.start()
                        first_token = False
                    full += tok
                    live_md.update(Markdown(full))

                if "tool_calls" in delta:
                    for tc in delta["tool_calls"]:
                        t_idx = tc.get("index", 0)
                        if t_idx not in tool_calls_dict:
                            fn = tc.get("function", {})
                            tool_calls_dict[t_idx] = {
                                "id": tc.get("id") or f"call_{t_idx}",
                                "type": tc.get("type") or "function",
                                "function": {
                                    "name": fn.get("name") or "",
                                    "arguments": fn.get("arguments") or ""
                                }
                            }
                        else:
                            if tc.get("id"):
                                tool_calls_dict[t_idx]["id"] = tc["id"]
                            if tc.get("type"):
                                tool_calls_dict[t_idx]["type"] = tc["type"]
                            if "function" in tc:
                                fn = tc["function"]
                                if fn.get("name"):
                                    tool_calls_dict[t_idx]["function"]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    tool_calls_dict[t_idx]["function"]["arguments"] += fn["arguments"]

                if "usage" in chunk: usage = chunk["usage"]
            except: continue

        if not first_token:
            live_md.stop()

        final_tool_calls = []
        if tool_calls_dict:
            for k in sorted(tool_calls_dict.keys()):
                tc = tool_calls_dict[k].copy()
                if "index" in tc: del tc["index"]
                if not tc.get("function", {}).get("arguments"):
                    tc["function"]["arguments"] = "{}"
                final_tool_calls.append(tc)
        else:
            final_tool_calls = None

        if not full and final_tool_calls and first_token:
            if stop_spinner is not None:
                stop_spinner.set()
                time.sleep(0.05)
            console.print(f"\n[bold magenta]🤖 RedX is using a tool...[/bold magenta]")

        # Mark model as UP on successful response
        with MODEL_STATUS_LOCK:
            MODEL_STATUS[model] = "UP"
        return full, usage, final_tool_calls

    except requests.Timeout:
        if stop_spinner is not None: stop_spinner.set()
        console.print(Panel(
            f"[red]⏰ [bold]{model.split('/')[-1]}[/bold] timed out (120s).[/red]\n"
            "Run [bold]/model next[/bold] to switch to a faster model.",
            title="Timeout", border_style="red"))
        return None, {}, None
    except Exception as e:
        if stop_spinner is not None: stop_spinner.set()
        console.print(f"[red]Error: {e}[/red]")
        return None, {}, None

# ── @file Injection ───────────────────────────────────────────────────────────
def inject_files(text: str) -> str:
    refs = re.findall(r"@([^\s]+)", text)
    if not refs: return text
    blocks = []
    injected = 0
    for ref in refs:
        # Support globs: @src/**/*.py
        matched = sorted(glob.glob(ref, recursive=True))
        if not matched:
            matched = [ref]
        for ps in matched:
            p = Path(ps)
            if p.exists() and p.is_file():
                sz = p.stat().st_size
                if sz > 100_000:
                    blocks.append(f"[{p}: SKIPPED — too large ({sz//1024}KB)]")
                else:
                    blocks.append(f"```\n# {p}\n{p.read_text(errors='replace')}\n```")
                    console.print(f"[dim cyan]📎 {p} ({sz}B)[/dim cyan]")
                    injected += 1
            elif p.is_dir():
                console.print(f"[dim yellow]⚠ {ref} is a directory — use @{ref}/**/* for glob[/dim yellow]")
            else:
                console.print(f"[dim red]⚠ Not found: {p}[/dim red]")
    if injected > 1:
        console.print(f"[dim cyan]📎 {injected} files injected[/dim cyan]")
    clean = re.sub(r"@[^\s]+", "", text).strip()
    return clean + "\n\n" + "\n\n".join(blocks)

# ── Context Bar ───────────────────────────────────────────────────────────────
def ctx_bar(pct: int) -> str:
    b = "█"*(pct//10) + "░"*(10-pct//10)
    c = "bold red" if pct>=90 else "yellow" if pct>=70 else "green"
    return f"[{c}][{b} {pct}%][/{c}]"

def update_tokens(session: Session, usage: dict) -> int:
    if not usage:
        usage = {}
    n = usage.get("prompt_tokens",0) + usage.get("completion_tokens",0)
    session.tokens += n
    session.cost   += n * 0.0000005
    return n

def print_meta(turn_tok: int, session: Session):
    console.print(f"[dim]  ↳ +{turn_tok:,} tokens | total {session.tokens:,} | {ctx_bar(session.ctx_pct)}[/dim]")

# ── Help Text ─────────────────────────────────────────────────────────────────
def print_help():
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    
    t = Table(box=box.SIMPLE, show_header=True, expand=False, border_style="dim", pad_edge=False)
    t.add_column("Category", style="bold cyan", width=14)
    t.add_column("Command", style="green", width=16)
    t.add_column("Args", style="dim", width=10)
    t.add_column("Description", style="white")

    # Session
    t.add_row("Session", "/new", "[name]", "Create a new session")
    t.add_row("", "/sessions", "", "List all sessions")
    t.add_row("", "/resume", "<name>", "Switch to session")
    t.add_row("", "/clear", "", "Wipe conversation history")
    t.add_row("", "/undo", "[file]", "Remove last exchange or restore file")
    t.add_row("", "/usage", "", "Show token and cost stats")
    
    t.add_section()
    # Context
    t.add_row("Context", "/compact", "", "Compress history using AI")
    t.add_row("", "/permit", "on/off", "Toggle auto-approval for bash")
    t.add_row("", "/debug", "", "Toggle debug logging")
    t.add_row("", "@path/file", "", "Inject file content into prompt")
    t.add_row("", "!!", "", "Re-run the last message")
    
    t.add_section()
    # Model
    t.add_row("Model", "/model", "", "List models and live heartbeat")
    t.add_row("", "/model", "<name|#>", "Switch active model")
    t.add_row("", "/model", "next", "Auto-switch to next UP model")
    
    t.add_section()
    # Git
    t.add_row("Git", "/git", "<args>", "Run a native git command")
    t.add_row("", "/git commit", "", "Generate AI commit message")

    t.add_section()
    # Skills
    t.add_row("Agent Skills", "/skill", "", "List all available skills")
    t.add_row("", "/skill", "<name>", "Load a specific skill")
    t.add_row("", "/skill", "<n> <ip>", "Load skill and set target")
    t.add_row("", "/pentest", "<target>", "Run full NIST-aligned pentest")
    
    t.add_section()
    # Jobs
    t.add_row("Jobs", "/jobs", "", "List background jobs")
    t.add_row("", "/jobs", "kill <id>", "Kill a specific background job")
    t.add_row("", "/jobs", "killall", "Kill all background jobs")

    t.add_section()
    # Prompts / Personas
    t.add_row("Prompt", "/prompt", "", "Select active Persona (TUI)")
    t.add_row("", "/prompt", "new <name>", "Generate a new Persona")

    t.add_section()
    # Memory & Cross-session
    t.add_row("Memory", "/memory", "show", "Display persistent memory")
    t.add_row("", "/memory", "save <fact>", "Save a fact to memory")
    t.add_row("", "/memory", "clear", "Wipe all memory")
    t.add_row("", "/memory", "delete <text>", "Remove matching memory lines")
    t.add_row("", "/load", "<session>", "Inject another session into context")

    t.add_section()
    # Other
    t.add_row("Other", "/help", "", "Show this help screen")
    t.add_row("", "/exit", "", "Quit RedX CLI")
    t.add_row("", "Ctrl+C", "", "Cancel AI stream / interrupt")
    t.add_row("", "--yes / -y", "", "CLI flag: Start with permit ON")

    console.print(Panel(t, title=f"[bold cyan]🔴 RedX CLI v{VERSION}[/bold cyan]", border_style="cyan", padding=(1, 2)))

def _gather_project_context(max_chars=30000):
    import os
    from pathlib import Path
    cwd = Path(os.getcwd())
    ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv', 'env', '.env', '.idea', '.vscode', 'build', 'dist', '.gemini'}
    
    tree = []
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        level = str(Path(root)).replace(str(cwd), '').count(os.sep)
        indent = ' ' * 4 * level
        tree.append(f"{indent}{os.path.basename(root)}/")
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            tree.append(f"{subindent}{f}")
            
    tree_str = "\n".join(tree[:200])
    if len(tree) > 200:
        tree_str += "\n    ... (truncated)"
        
    context = f"Project Directory Tree:\n{tree_str}\n\n"
    
    key_files = ['README.md', 'package.json', 'requirements.txt', 'config.json', 'docker-compose.yml', 'Makefile', 'pyproject.toml']
    for kf in key_files:
        p = cwd / kf
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8")
                context += f"--- {kf} ---\n{content[:5000]}\n\n"
            except:
                pass

    return context[:max_chars]

# ── Slash Commands ────────────────────────────────────────────────────────────
def handle_slash(cmd_str: str, session: Session, api_key: str,
                 debug: list, auto_yes: list, hooks: dict = None) -> tuple:
    """Returns (handled, should_exit)"""
    if hooks is None: hooks = {}
    parts = cmd_str.strip().split(maxsplit=1)
    cmd = parts[0].lower(); arg = parts[1].strip() if len(parts)>1 else ""

    if cmd in ("/help","/?"):
        print_help(); return True, False

    if cmd in ("/exit","/quit"):
        return True, True

    if cmd == "/clear":
        session.reset()
        console.print("[yellow]🗑  History cleared.[/yellow]"); return True, False

    if cmd == "/undo":
        if arg.startswith("file "):
            path = arg.split(" ", 1)[1].strip()
            if path in FILE_BACKUPS:
                import shutil
                shutil.copy2(FILE_BACKUPS[path], path)
                console.print(f"[green]✅ Restored {path} from backup.[/green]")
            else:
                console.print(f"[red]No backup found for {path}.[/red]")
            return True, False
            
        msgs, removed = session.messages, 0
        for role in ["assistant","user"]:
            for i in range(len(msgs)-1,-1,-1):
                if msgs[i]["role"]==role: msgs.pop(i); removed+=1; break
        if removed: session.save(); console.print(f"[yellow]↩  Undone ({removed} messages).[/yellow]")
        else: console.print("[dim]Nothing to undo.[/dim]")
        return True, False

    if cmd == "/usage":
        t = Table(box=box.ROUNDED, show_lines=True, title="📊 Usage")
        t.add_column("Metric",style="cyan"); t.add_column("Value",style="white")
        t.add_row("Session", session.name)
        t.add_row("Tokens",  f"{session.tokens:,}")
        t.add_row("Est. Cost", "[green]Free (OpenRouter :free tier)[/green]")
        t.add_row("Exchanges", str(session.user_count))
        t.add_row("Context", f"{session.ctx_pct}%  {ctx_bar(session.ctx_pct)}")
        t.add_row("Model", session.models[0] if session.models else "none")
        console.print(t); return True, False

    if cmd == "/sessions":
        rows = list_sessions()
        if not rows: console.print("[dim]No sessions.[/dim]")
        else:
            t = Table(box=box.ROUNDED, show_lines=True, title="📁 Sessions")
            t.add_column("Name", style="cyan")
            t.add_column("Created", style="dim", width=11)
            t.add_column("Updated", style="dim", width=17)
            t.add_column("Msgs", justify="right", width=5)
            t.add_column("Tokens", justify="right", width=10)
            t.add_column("Model", style="dim")
            t.add_column("KB", justify="right", width=5)
            for s in rows:
                n = s["name"] + (" [green]★[/green]" if s["name"]==session.name else "")
                t.add_row(n, s.get("created","?"), s["updated"],
                          str(s["msgs"]), f"{s['tokens']:,}",
                          s.get("model","?"), str(s.get("size_kb",0)))
            console.print(t)
        return True, False

    if cmd == "/new":
        name = arg or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        session.name = name; session.path = SESSIONS_DIR/f"{name}.json"
        session.reset()
        console.print(f"[green]✨ Session: [bold]{name}[/bold][/green]"); return True, False

    if cmd == "/rename":
        if not arg:
            console.print("[red]Usage: /rename <new_name>[/red]"); return True, False
        new_name = re.sub(r"[^a-zA-Z0-9_-]", "_", arg)
        new_path = SESSIONS_DIR / f"{new_name}.json"
        if new_path.exists():
            console.print(f"[red]Session '{new_name}' already exists.[/red]"); return True, False
        
        if session.path and session.path.exists():
            session.path.rename(new_path)
            
        old_name = session.name
        session.name = new_name
        session.path = new_path
        session.save()
        console.print(f"[green]✅ Session renamed: [bold]{old_name}[/bold] ➔ [bold]{new_name}[/bold][/green]")
        return True, False

    if cmd == "/resume":
        if not arg:
            handle_slash("/sessions", session, api_key, debug, auto_yes, hooks)
            console.print("[dim]/resume <name>[/dim]"); return True, False
        p = SESSIONS_DIR/f"{arg}.json"
        if not p.exists(): console.print(f"[red]Session '{arg}' not found.[/red]"); return True, False
        session.name=arg; session.path=p; session.load()
        console.print(f"[green]▶  Resumed: [bold]{arg}[/bold] ({session.user_count} exchanges)[/green]")
        return True, False

    if cmd in ("/delete", "/del"):
        if not arg:
            console.print("[red]Usage: /delete <session_name>[/red]"); return True, False
        p = SESSIONS_DIR / f"{arg}.json"
        if not p.exists():
            console.print(f"[red]Session '{arg}' not found.[/red]"); return True, False
        try:
            p.unlink()
            console.print(f"[green]🗑  Session [bold]{arg}[/bold] deleted.[/green]")
            if session.name == arg:
                name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                session.name = name; session.path = SESSIONS_DIR / f"{name}.json"
                session.reset()
                console.print(f"[dim]Switched to new session: {name}[/dim]")
        except Exception as e:
            console.print(f"[red]Error deleting session: {e}[/red]")
        return True, False

    if cmd == "/compact":
        if len(session.messages) < 4:
            console.print("[dim]Not enough history.[/dim]"); return True, False
        console.print("[yellow]⏳ Compacting...[/yellow]")
        orig = list(session.messages)
        non_sys = [m for m in orig if m["role"] != "system"]

        # Slice to last 40 messages so the summary request fits free model contexts
        SAMPLE = 40
        sample_msgs = non_sys[-SAMPLE:] if len(non_sys) > SAMPLE else non_sys
        sample_text = "\n\n".join(
            f"[{m['role'].upper()}]: {str(m.get('content',''))[:800]}"
            for m in sample_msgs
        )

        # Build a small one-shot session just for the summary call
        from copy import deepcopy
        tmp = deepcopy(session)
        tmp.messages = [
            {"role": "system",  "content": SYSTEM_PROMPT},
            {"role": "user",    "content":
                f"Below is a sample of the conversation history ({len(orig)} messages total).\n"
                f"Summarize it into a compact context block.\n"
                f"Preserve all technical decisions, code, file paths, commands, and key facts.\n\n"
                f"{sample_text}"}
        ]

        summary, usage, _ = stream_response(tmp, api_key)
        if summary:
            update_tokens(session, usage)
            last4 = non_sys[-4:]
            session.messages = [
                {"role": "system",   "content": SYSTEM_PROMPT},
                {"role": "user",     "content": f"[Compact Context]\n{summary}"},
                {"role": "assistant","content": "Understood. I have full context."},
            ] + last4
            session.save()
            console.print(f"[green]✅ Compacted (AI summary): {len(orig)} → {len(session.messages)} messages[/green]")
        else:
            # AI unavailable (rate-limited) → hard truncate: keep last 20 messages
            KEEP = 20
            last_n = non_sys[-KEEP:]
            session.messages = [{"role": "system", "content": SYSTEM_PROMPT}] + last_n
            session.save()
            console.print(Panel(
                f"[yellow]⚠ AI was unavailable (rate-limited) — used hard truncate.[/yellow]\n"
                f"Kept last {len(last_n)} messages (dropped {len(orig) - len(last_n) - 1} older ones).\n"
                f"[dim]Run /compact again when models are available to get an AI summary.[/dim]",
                title="✅ Compacted (Hard Truncate)", border_style="yellow"
            ))
        return True, False

    if cmd == "/model":
        if not arg:
            from prompt_toolkit.shortcuts import radiolist_dialog

            # Build choices from LIVE_MODELS (not just session.models)
            # Session-active model shows first, rest follow
            all_live_ids = [m["id"] for m in LIVE_MODELS]
            # Merge: session models first (preserving order), then any new live models
            merged_ids = list(session.models)
            for mid in all_live_ids:
                if mid not in merged_ids:
                    merged_ids.append(mid)
            # Update session with any newly discovered models
            session.models = merged_ids

            # --- Custom Tabbed TUI for /model ---
            from prompt_toolkit.application import Application
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import Layout, HSplit, Window
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.widgets import RadioList, Frame
            from prompt_toolkit.mouse_events import MouseEventType
            from prompt_toolkit.layout.containers import ConditionalContainer
            from prompt_toolkit.filters import Condition
            from prompt_toolkit.styles import Style

            or_models = [m for m in merged_ids if get_model_provider(m) == "openrouter"]
            nv_models = [m for m in merged_ids if get_model_provider(m) == "nvidia"]

            active = session.models[0] if session.models else None

            if active in or_models:
                or_models.remove(active)
                or_models.insert(0, active)
            elif active in nv_models:
                nv_models.remove(active)
                nv_models.insert(0, active)

            state = {"tab": "openrouter" if (active in or_models or not nv_models) else "nvidia"}

            def get_choices(models):
                c = []
                for m in models:
                    s        = MODEL_STATUS.get(m, "UNKNOWN")
                    if s == "DOWN" and m != active:
                        continue
                    ctx_k    = MODEL_CTX.get(m, 0) // 1024
                    ctx_str  = f"{ctx_k}K" if ctx_k else "?"
                    icons    = {"UP": "🟢 UP", "LIMIT": "🟡 LIM", "DOWN": "🔴 DWN", "UNKNOWN": "⬜ ..."}
                    checked  = _ago(MODEL_LAST_CHECK.get(m))
                    m_name   = m.split('/')[-1]
                    if len(m_name) > 38: m_name = m_name[:35] + "..."
                    label    = f"{m_name:<38} │ {icons[s]:<9} │ {ctx_str:>5} │ {checked}"
                    c.append((m, label))
                return c

            or_radiolist = RadioList(get_choices(or_models))
            nv_radiolist = RadioList(get_choices(nv_models))

            if active in or_models: or_radiolist.current_value = active
            if active in nv_models: nv_radiolist.current_value = active

            def click_tab(mouse_event, tab_name):
                if mouse_event.event_type == MouseEventType.MOUSE_UP:
                    state["tab"] = tab_name
                    app.layout.focus(get_active_radiolist())

            with MODEL_STATUS_LOCK:
                cyc_done = HEARTBEAT_CYCLE[1]
            if cyc_done is None:
                hb_line = "⟳ Heartbeat running now..."
            else:
                elapsed = int((datetime.now() - cyc_done).total_seconds())
                nxt = max(0, HEARTBEAT_INTERVAL - elapsed)
                hb_line = f"💓 Last check: {_ago(cyc_done)} | Next in: {nxt}s"

            def get_navbar_text():
                or_style = "class:tab-active" if state["tab"] == "openrouter" else "class:tab-inactive"
                nv_style = "class:tab-active" if state["tab"] == "nvidia" else "class:tab-inactive"
                return [
                    (or_style, f"  [ OpenRouter ({len(or_models)}) ]  ", lambda ev: click_tab(ev, "openrouter")),
                    ("", " "),
                    (nv_style, f"  [ NVIDIA ({len(nv_models)}) ]  ", lambda ev: click_tab(ev, "nvidia")),
                    ("", f"    {hb_line}")
                ]

            navbar = Window(height=1, content=FormattedTextControl(get_navbar_text))

            def get_active_radiolist():
                return or_radiolist if state["tab"] == "openrouter" else nv_radiolist

            body = Frame(
                title="Model Selection",
                body=HSplit([
                    navbar,
                    Window(height=1, char="-"),
                    ConditionalContainer(or_radiolist, filter=Condition(lambda: state["tab"] == "openrouter")),
                    ConditionalContainer(nv_radiolist, filter=Condition(lambda: state["tab"] == "nvidia")),
                    Window(height=1, char="-"),
                    Window(height=1, content=FormattedTextControl("Use ↑/↓/Scroll. Press Enter to select, Esc to cancel, TAB to switch providers."))
                ])
            )

            kb = KeyBindings()

            @kb.add("tab")
            @kb.add("right")
            @kb.add("left")
            def _(event):
                state["tab"] = "nvidia" if state["tab"] == "openrouter" else "openrouter"
                event.app.layout.focus(get_active_radiolist())

            @kb.add("enter", eager=True)
            def _(event):
                rl = get_active_radiolist()
                if rl.values:
                    val = rl.values[rl._selected_index][0]
                    event.app.exit(result=val)
                else:
                    event.app.exit(result=None)

            @kb.add("escape")
            def _(event):
                event.app.exit(result=None)

            app = Application(
                layout=Layout(body),
                key_bindings=kb,
                full_screen=True,
                mouse_support=True,
                style=Style.from_dict({
                    "tab-active": "bg:#ffffff #000000 bold",
                    "tab-inactive": "bg:#444444 #aaaaaa",
                })
            )
            
            try:
                app.layout.focus(get_active_radiolist())
            except: pass
            
            result = app.run()

            if result and result != active:
                if result in session.models:
                    session.models.remove(result)
                session.models.insert(0, result)
                provider = get_model_provider(result)
                badge = "[OpenRouter]" if provider == "openrouter" else "[NVIDIA]"
                console.print(f"[green]⚡ Switched to {badge}: {result.split('/')[-1]}[/green]")
                session.save()
            elif result is None:
                console.print("[dim]Model selection cancelled.[/dim]")

            return True, False
        elif arg == "next":
            active = session.models[0] if session.models else ""
            found = False
            for m in session.models[1:] + [session.models[0]]:
                if MODEL_STATUS.get(m) == "UP" and m != active:
                    session.models.remove(m)
                    session.models.insert(0, m)
                    console.print(f"[green]⚡ Switched to: {m.split('/')[-1]}[/green] 🟢")
                    found = True; break
            if not found:
                console.print("[yellow]No UP model found. Try /model <name>.[/yellow]")
            session.save()
        elif arg == "best":
            for m in DEFAULT_MODELS:
                if MODEL_STATUS.get(m) == "UP" and get_model_provider(m) == "openrouter":
                    if m in session.models: session.models.remove(m)
                    session.models.insert(0, m)
                    console.print(f"[green]⚡ Best available: {m.split('/')[-1]}[/green] 🟢")
                    session.save(); break
            else:
                console.print("[yellow]No UP model yet. Wait ~60s for heartbeat.[/yellow]")
        elif arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(session.models):
                m = session.models.pop(idx)
                session.models.insert(0, m)
                s = MODEL_STATUS.get(m, "UNKNOWN")
                icons = {"UP":"🟢","LIMIT":"🟡","DOWN":"🔴","UNKNOWN":"⬜"}
                console.print(f"[green]⚡ Switched to #{arg}: {m.split('/')[-1]}[/green] {icons[s]}")
                session.save()
            else:
                console.print("[red]Invalid number. Use /model to see list.[/red]")
        else:
            matched = False
            for i, m in enumerate(session.models):
                if arg.lower() in m.lower():
                    session.models.insert(0, session.models.pop(i))
                    s = MODEL_STATUS.get(m, "UNKNOWN")
                    icons = {"UP":"🟢","LIMIT":"🟡","DOWN":"🔴","UNKNOWN":"⬜"}
                    console.print(f"[green]⚡ Switched to: {m.split('/')[-1]}[/green] {icons[s]}")
                    matched = True; break
            if not matched:
                session.models.insert(0, arg)
                console.print(f"[green]⚡ Added custom model: {arg}[/green]")
            session.save()
        return True, False

    if cmd == "/debug":
        debug[0] = not debug[0]
        console.print(f"[cyan]Debug: {'[green]ON[/green]' if debug[0] else '[red]OFF[/red]'}[/cyan]")
        return True, False

    if cmd == "/permit":
        if arg.lower() in ("on", "1", "true", ""):
            auto_yes[0] = True
            console.print(Panel(
                "[bold green]🔓 PERMIT MODE ON[/bold green]\n"
                "[dim]RedX will auto-run all bash commands without asking.\n"
                "Destructive commands (rm -rf /, etc.) are still blocked.[/dim]\n"
                "Run [bold]/permit off[/bold] to return to approval mode.",
                border_style="green", title="Permit Mode"
            ))
        else:
            auto_yes[0] = False
            console.print(Panel(
                "[bold yellow]🔒 PERMIT MODE OFF[/bold yellow]\n"
                "[dim]RedX will ask for approval before running each command.[/dim]",
                border_style="yellow", title="Permit Mode"
            ))
        return True, False

    if cmd == "/git":
        if not arg:
            console.print("[dim]/git <args>  e.g. /git status | /git diff | /git commit[/dim]")
            return True, False
        if arg == "commit":
            diff = subprocess.run("git diff --staged || git diff", shell=True,
                                  capture_output=True, text=True, timeout=10).stdout
            if not diff.strip(): console.print("[dim]No changes.[/dim]"); return True, False
            console.print(f"[dim]{diff[:1500]}[/dim]")
            tmp = [{"role":"system","content":"Generate a Conventional Commits message. Reply ONLY with the message."},
                   {"role":"user","content":f"Diff:\n```\n{diff[:3000]}\n```"}]
            orig_msgs = session.messages
            session.messages = tmp
            msg, _ = stream_response(session, api_key)
            session.messages = orig_msgs
            if msg:
                msg = msg.strip().strip('"\'')
                r = subprocess.run(f'git commit -m "{msg}"', shell=True, capture_output=True, text=True)
                console.print(f"[green]✅ {msg}[/green]")
                if r.stdout: console.print(f"[dim]{r.stdout}[/dim]")
        else:
            r = subprocess.run(f"git {arg}", shell=True, capture_output=True, text=True, timeout=30)
            out = r.stdout + r.stderr
            console.print(f"[dim]{out}[/dim]" if out.strip() else "[dim](no output)[/dim]")
        return True, False

    # /skill — skill loader
    if cmd in ("/skill", "/skills"):
        if not arg or arg.strip() == "list":
            skills = list_tact_skills()
            if not skills:
                console.print(
                    "[bold red]Skills not installed.[/bold red] Run:\n"
                    "  [cyan]cd redx_cli && git clone "
                    "https://github.com/transilienceai/communitytools.git libs/tact[/cyan]"
                )
                return True, False
            t = Table(box=box.ROUNDED, show_lines=True,
                      title="[bold cyan]🛡 Agent Skills[/bold cyan]")
            t.add_column("Skill", style="cyan", width=30)
            t.add_column("Description", style="white")
            for s in skills:
                t.add_row(s["name"], s["description"])
            console.print(t)
            console.print("[dim]Usage: /skill <name> [target]  e.g. /skill recon 10.10.10.1[/dim]")
            return True, False

        # Parse: /skill <skill_name> [target]
        parts2   = arg.split(maxsplit=1)
        sname    = parts2[0].strip()
        s_target = parts2[1].strip() if len(parts2) > 1 else session.data.get("target", "")

        content = load_tact_skill(sname)
        if content is None:
            console.print(
                f"[red]Skill '[bold]{sname}[/bold]' not found.[/red] "
                f"Run [cyan]/skill list[/cyan] to see all skills."
            )
            return True, False

        # Persist target across skill calls
        if s_target:
            session.data["target"] = s_target
            session.save()

        ctx_line = f"TARGET: {s_target}\n\n" if s_target else ""
        if s_target:
            injection = (
                f"[Skill: {sname}]\n\n"
                f"{ctx_line}"
                f"You are operating as an autonomous security agent. "
                f"The following is your skill guide — follow it step by step.\n"
                f"Execute each command using <EXECUTE> blocks, await output, then continue.\n"
                f"Chain commands with && where possible. When done, report findings.\n\n"
                f"--- SKILL GUIDE ---\n{content}\n--- END GUIDE ---\n\n"
                f"Begin Phase 1 now."
            )
        else:
            injection = (
                f"[Skill: {sname}]\n\n"
                f"Skill guide loaded. What is the target? "
                f"(Tip: /skill {sname} <target> to set it automatically)"
            )

        console.print(
            f"[bold green]🛡 Skill '[bold cyan]{sname}[/bold cyan]' loaded"
            + (f"  [dim]→ target: {s_target}[/dim]" if s_target else "")
            + "[/bold green]"
        )

        # Inject and run the AI loop
        session.messages.append({"role": "user", "content": injection})
        session.save()
        _ai_loop(session, api_key, debug, auto_yes, hooks)
        return True, False

    # /tact — shortcut for full pentest coordination skill
    if cmd == "/tact":
        if not arg:
            console.print(
                "[dim]/pentest <target>  — Full NIST-aligned pentest via coordination skill[/dim]\n"
                "[dim]Example: /tact 10.10.10.1[/dim]"
            )
            return True, False
        return handle_slash(f"/skill coordination {arg}", session, api_key, debug, auto_yes, hooks)


    # ── /memory — Global persistent memory across sessions ──────────────────
    if cmd == "/memory":
        mem_file = REDX_DIR / "memory.md"

        if not arg or arg == "show":
            if mem_file.exists() and mem_file.read_text(errors="replace").strip():
                content = mem_file.read_text(errors="replace")
                console.print(Panel(Markdown(content),
                    title="[bold cyan]🧠 Persistent Memory[/bold cyan]",
                    border_style="cyan"))
            else:
                console.print("[dim]Memory is empty. Use /memory save <fact> to add entries.[/dim]")
            return True, False

        if arg == "clear":
            if mem_file.exists():
                mem_file.write_text("")
            console.print("[yellow]🗑  Memory cleared.[/yellow]")
            return True, False

        if arg.startswith("save "):
            fact = arg[5:].strip()
            if not fact:
                console.print("[red]Usage: /memory save <fact>[/red]")
                return True, False
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = f"- [{now}] [{session.name}] {fact}\n"
            with open(mem_file, "a") as f:
                f.write(entry)
            console.print(f"[green]🧠 Saved to memory:[/green] {fact}")
            return True, False

        if arg.startswith("delete "):
            needle = arg[7:].strip().lower()
            if mem_file.exists():
                lines = mem_file.read_text(errors="replace").splitlines(keepends=True)
                kept = [l for l in lines if needle not in l.lower()]
                dropped = len(lines) - len(kept)
                mem_file.write_text("".join(kept))
                console.print(f"[yellow]Removed {dropped} line(s) containing '{needle}'.[/yellow]")
            return True, False

        console.print("[dim]Usage: /memory show | /memory save <fact> | /memory clear | /memory delete <text>[/dim]")
        return True, False

    # ── /load — Inject another session's history into current context ────────
    if cmd == "/load":
        if not arg:
            rows = list_sessions()
            t = Table(title="Sessions available to load", box=box.SIMPLE, border_style="dim")
            t.add_column("Name", style="cyan"); t.add_column("Msgs", width=5)
            t.add_column("Updated", style="dim"); t.add_column("Model", style="dim")
            for s in rows:
                if s["name"] != session.name:
                    t.add_row(s["name"], str(s["msgs"]), s["updated"], s.get("model","?"))
            console.print(t)
            console.print("[dim]Usage: /load <session_name>[/dim]")
            return True, False

        target_path = SESSIONS_DIR / f"{arg}.json"
        if not target_path.exists():
            console.print(f"[red]Session '{arg}' not found.[/red]")
            return True, False

        try:
            other = json.loads(target_path.read_text())
            msgs = [m for m in other.get("messages", []) if m["role"] != "system"]
            if not msgs:
                console.print(f"[dim]Session '{arg}' has no messages to load.[/dim]")
                return True, False

            # Build a compact summary of the other session
            sample = "\n".join(
                f"[{m['role'].upper()}]: {str(m.get('content',''))[:500]}"
                for m in msgs[-30:]  # last 30 messages
            )
            injection = (
                f"[Cross-Session Memory Loaded: '{arg}']\n"
                f"The following is a summary of work done in session '{arg}'. "
                f"Use this context to inform your current work:\n\n{sample}\n"
                f"[End of session '{arg}' context]"
            )
            session.messages.append({"role": "user", "content": injection})
            session.messages.append({"role": "assistant", "content":
                f"Understood. I've loaded context from session '{arg}' "
                f"({len(msgs)} messages). I'll use this to inform my current work."})
            session.save()
            console.print(Panel(
                f"[green]✅ Loaded [bold]{len(msgs)}[/bold] messages from session "
                f"'[bold cyan]{arg}[/bold cyan]' into current context.[/green]\n"
                f"[dim]RedX now has awareness of that session's work.[/dim]",
                border_style="green", title="Session Loaded"))
        except Exception as e:
            console.print(f"[red]Error loading session: {e}[/red]")
        return True, False
    # ── /prompt — Switch or Generate Global Personas ─────────────────────────
    if cmd == "/prompt":
        if arg.startswith("delete "):
            name = arg[7:].strip().lower()
            if not name:
                console.print("[red]Usage: /prompt delete <name>[/red]")
                return True, False
            if name == "default":
                console.print("[red]Cannot delete the default prompt.[/red]")
                return True, False
                
            out_path = PROMPTS_DIR / f"{name}.md"
            if not out_path.exists():
                console.print(f"[red]Prompt '{name}' not found.[/red]")
                return True, False
                
            out_path.unlink()
            console.print(f"[green]🗑️  Deleted prompt '{name}'.[/green]")
            return True, False

        if arg.startswith("new "):
            parts = arg[4:].strip().split(maxsplit=1)
            if not parts:
                console.print("[red]Usage: /prompt new <name> [optional instructions][/red]")
                return True, False
                
            name = parts[0].lower()
            name = re.sub(r"[^a-z0-9_-]", "_", name)
            instruction = parts[1].strip() if len(parts) > 1 else ""
            
            if not name:
                console.print("[red]Usage: /prompt new <name> [optional instructions][/red]")
                return True, False
            
            out_path = PROMPTS_DIR / f"{name}.md"
            if out_path.exists():
                console.print(f"[red]Prompt '{name}' already exists.[/red]")
                return True, False

            if instruction:
                console.print(f"[yellow]⏳ Generating custom persona '{name}' based on your instructions...[/yellow]")
                prompt = (
                    f"Generate a RedX system persona file for a profile named '{name}'.\n"
                    f"Follow these strict user instructions to create the persona:\n"
                    f"\"{instruction}\"\n\n"
                    "The file should define:\n"
                    "1. Who the user is based on the instructions\n"
                    "2. Preferred tools and methodology\n"
                    "3. Rules RedX must always follow in this persona\n\n"
                    "Make it practical and concise. Use markdown headers.\n"
                    "Output ONLY the raw markdown content, nothing else."
                )
            else:
                console.print(f"[yellow]⏳ Scanning current directory to analyze project architecture...[/yellow]")
                project_context = _gather_project_context()

                console.print(f"[yellow]⏳ Generating context-aware persona '{name}'...[/yellow]")
                prompt = (
                    f"You are generating a highly specialized RedX system persona file for a profile named '{name}'.\n"
                    f"Below is the complete context of the user's current project directory, including its architecture, key files, and technical stack:\n\n"
                    f"--- PROJECT CONTEXT START ---\n"
                    f"{project_context}\n"
                    f"--- PROJECT CONTEXT END ---\n\n"
                    "Based heavily on the above project context, generate a tailored persona file.\n"
                    "The file should define:\n"
                    "1. Who the user is (derive from the project context)\n"
                    "2. Preferred tools and methodology for this specific project/persona\n"
                    "3. Architecture rules, technical constraints, and coding/hacking style preferences observed in the project\n"
                    "4. Rules RedX must always follow in this persona to maintain consistency with the project\n\n"
                    "Make it practical, highly specific to the analyzed project, and concise. Use markdown headers.\n"
                    "Output ONLY the raw markdown content, nothing else."
                )
            
            from copy import deepcopy
            tmp = deepcopy(session)
            tmp.messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
            console.print("[dim]🤖 Generating...[/dim]")
            result, usage, _ = stream_response(tmp, api_key)
            update_tokens(session, usage)
            
            if not result:
                console.print("[red]❌ Generation failed.[/red]")
                return True, False
                
            clean = re.sub(r"^```(?:markdown)?\n?", "", result.strip())
            clean = re.sub(r"\n?```$", "", clean.strip())
            out_path.write_text(clean, encoding="utf-8")
            
            console.print(Panel(
                f"[green]✅ Generated [bold]{name}.md[/bold][/green]\n\n"
                f"[dim]Use '/prompt' to switch to it.[/dim]",
                title="[bold cyan]📋 Prompt Created[/bold cyan]", border_style="cyan"))
            return True, False
            
        elif not arg:
            from prompt_toolkit.shortcuts import radiolist_dialog
            
            prompts = get_available_prompts()
            if not prompts:
                console.print("[red]No prompts found.[/red]")
                return True, False
                
            choices = [(p, f"{p}{' (active)' if p == session.prompt else ''}") for p in prompts]
            
            result = radiolist_dialog(
                title="Persona Selection",
                text="Use ↑/↓ arrows to navigate. Press Enter to select.\nUse '/prompt new <name>' to generate a new one.",
                values=choices,
                default=session.prompt
            ).run()
            
            if result and result != session.prompt:
                session.prompt = result
                session.save()
                console.print(f"[green]🎭 Switched persona to: {result}[/green]")
            elif result is None:
                console.print("[dim]Selection cancelled.[/dim]")
            
            return True, False
            
        console.print("[dim]Usage: /prompt | /prompt new <name>[/dim]")
        return True, False

    return False, False



# ── AI Loop (shared by process_turn and /skill) ───────────────────────────────
def _auto_trim(session: Session):
    """Silently drop oldest non-system messages when context > 80%."""
    ctx_limit = MODEL_CTX.get(session.models[0] if session.models else "", 128_000)
    thresh = int(ctx_limit * 0.80)
    while True:
        chars = sum(len(str(m.get("content", ""))) for m in session.messages)
        est_tokens = chars // 4
        if est_tokens <= thresh:
            break
        # Find oldest non-system message after the first user message
        non_sys = [i for i, m in enumerate(session.messages) if m["role"] != "system"]
        if len(non_sys) <= 4:
            break  # keep at least 4 messages
        dropped_idx = non_sys[0]
        session.messages.pop(dropped_idx)
    return session

def _ai_loop(session: Session, api_key: str, debug: list, auto_yes: list, hooks: dict):
    """Shared AI execution loop with Native Tool Calling."""
    while True:
        _auto_trim(session)  # auto-trim before each API call
        if debug[0]:
            console.print(f"[dim cyan][DEBUG] {len(session.messages)} msgs → {session.models[0]}[/dim cyan]")

        _cancel.clear()
        stop_spinner = threading.Event()

        def _run_spinner():
            from rich.text import Text
            frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
            i = 0
            with Live(console=console, refresh_per_second=12, transient=True) as live:
                while not stop_spinner.is_set():
                    live.update(
                        Text(f"🤖 RedX: {frames[i % 10]} Thinking...",
                             style="bold magenta")
                    )
                    stop_spinner.wait(timeout=0.08)
                    i += 1

        spin_thread = threading.Thread(target=_run_spinner, daemon=True)
        spin_thread.start()

        response, usage, tool_calls = stream_response(session, api_key, stop_spinner)
        stop_spinner.set()
        spin_thread.join(timeout=0.3)

        if _cancel.is_set():
            console.print("\n[yellow]⚡ Cancelled.[/yellow]")
            return

        if not response and not tool_calls:
            break

        assistant_msg = {"role": "assistant"}
        if response:
            assistant_msg["content"] = response
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        session.messages.append(assistant_msg)
        
        tok = update_tokens(session, usage)
        session.save()
        if response:
            print_meta(tok, session)

        if not tool_calls:
            break

        for tc in tool_calls:
            func_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except:
                args = {}
                
            console.print(f"\n[dim cyan]🔧 Calling tool: {func_name}[/dim cyan]")
            out = execute_native_tool(func_name, args, hooks, auto_yes, session)
            out_str = str(out)
            
            # Anti-loop state tracking
            if not hasattr(session, "consecutive_errors"):
                session.consecutive_errors = 0

            # Enrich tool errors with recovery suggestions for the AI
            if out_str.startswith("Error:") or "TIMEOUT" in out_str or "Exit 1" in out_str:
                session.consecutive_errors += 1
                if "TIMEOUT" in out_str or "critically low" in out_str:
                    out_str += "\n\n[SYSTEM NOTE] Resource limit reached. Do not retry this tool."
                elif session.consecutive_errors >= 2:
                    out_str += "\n\n[SYSTEM NOTE] You have failed multiple times. Stop and ask the user for guidance."
                else:
                    out_str += ("\n\n[SYSTEM NOTE] The tool returned an error. "
                                "Analyse the error message, correct your approach, and retry. ")
            else:
                session.consecutive_errors = 0
                
            session.messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": func_name,
                "content": out_str
            })
            
        session.save()

def process_turn(session: Session, user_input: str, api_key: str,
                 debug: list, auto_yes: list, hooks: dict, last_input: list):
    # Refresh system prompt date/time on every turn without wiping injected context
    if session.messages and session.messages[0]["role"] == "system":
        # We only update the dynamic time block, preserving Persona/Memory/Tree
        old_content = session.messages[0]["content"]
        base_prompt = build_system_prompt()
        
        # Everything after [Workspace Directory Tree:], [Active Persona:], or [Persistent Memory] is dynamic context
        # We want to replace the top part (the base prompt) but keep the bottom part
        parts = re.split(r"(?=\n\n\[(?:Workspace Directory Tree:|Git Status:|Active Persona:|Persistent Memory))", old_content, maxsplit=1)
        if len(parts) > 1:
            session.messages[0]["content"] = base_prompt + parts[1]
        else:
            session.messages[0]["content"] = base_prompt
    user_input = inject_files(user_input)
    session.messages.append({"role": "user", "content": user_input})
    # Session auto-name: rename "default" based on first message topic
    if session.name == "default" and session.user_count == 0:
        slug = re.sub(r"[^a-z0-9]+", "_", user_input[:40].lower()).strip("_")
        if slug:
            new_name = slug[:30]
            new_path = SESSIONS_DIR / f"{new_name}.json"
            if not new_path.exists():
                session.name = new_name
                session.path = new_path
                console.print(f"[dim]📝 Session auto-named: {new_name}[/dim]")
    session.save()
    _ai_loop(session, api_key, debug, auto_yes, hooks)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(prog="redx", description=f"RedX CLI v{VERSION}")
    parser.add_argument("prompt", nargs="*", help="Initial prompt")
    parser.add_argument("--session","-s", default="default", help="Session name")
    parser.add_argument("--continue","--resume","-c", dest="resume",
                        action="store_true", help="Continue last session")
    parser.add_argument("--yes","-y", action="store_true", help="Auto-approve all commands")
    parser.add_argument("--debug","-d", action="store_true", help="Debug mode")
    parser.add_argument("--list-sessions", action="store_true", help="List sessions and exit")
    args = parser.parse_args()

    if args.list_sessions:
        rows = list_sessions()
        if not rows: print("No sessions."); return
        for s in rows:
            print(f"  {s['name']:<25} {s['updated']}  {s['msgs']} msgs  {s['tokens']:,} tokens")
        return

    api_key, nv_key = load_api_keys()
    if not api_key:
        console.print("[bold red]OPENROUTER_API_KEY not found.[/bold red]"); sys.exit(1)

    # Load model list: disk cache first (instant), background refresh always runs
    cache_hit = _load_models_cache()
    if not cache_hit:
        # First ever run — seed with static fallback immediately
        _models_from_cache(
            [{"id": m, "provider": "openrouter", "ctx": 131_072, "name": m} for m in _STATIC_OPENROUTER_MODELS] +
            [{"id": m, "provider": "nvidia",     "ctx": 131_072, "name": m} for m in _STATIC_NVIDIA_MODELS]
        )
        console.print("[dim]📡 Fetching live model list in background...[/dim]")

    # Session init
    session = Session(args.session)
    if args.resume:
        # find most recently updated session
        rows = list_sessions()
        if rows:
            session = Session(rows[0]["name"])
    session.load()
    if not session.messages:
        session.reset()

    # Project context: Tree, Git, and REDX.md
    sys_content = session.messages[0].get("content", "") if session.messages else ""
    
    # 1. Directory Tree Context
    if "Directory Tree:" not in sys_content:
        try:
            # Get up to 3 levels deep, ignore common large dirs
            tree_cmd = "tree -L 3 -I 'node_modules|.git|__pycache__|venv|env|.venv'"
            tree_out = subprocess.check_output(tree_cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
            sys_content += f"\n\n[Workspace Directory Tree:]\n{tree_out}"
            console.print("[dim cyan]🌳 Workspace tree loaded[/dim cyan]")
        except Exception: pass
        
    # 2. Git Context (status + diff of changed files)
    if "Git Status:" not in sys_content:
        if Path(".git").exists():
            try:
                git_status = subprocess.check_output("git status -s", shell=True, text=True)
                if git_status.strip():
                    sys_content += f"\n\n[Git Status:]\n{git_status}"
                    # Also pull diff for modified files (first 3000 chars)
                    try:
                        git_diff = subprocess.check_output(
                            "git diff HEAD --stat --unified=2", shell=True, text=True)
                        if git_diff.strip():
                            sys_content += f"\n\n[Git Diff (HEAD):]\n{git_diff[:3000]}"
                    except Exception:
                        pass
                    console.print("[dim cyan]📦 Git context loaded[/dim cyan]")
            except Exception: pass

    ensure_default_prompt()

    # 3. Load Active Persona
    persona_content = load_active_prompt(session.prompt)
    if persona_content and "[Active Persona]" not in sys_content:
        sys_content += f"\n\n[Active Persona: {session.prompt}]\n{persona_content}"
        console.print(f"[dim green]🎭 Persona loaded: {session.prompt}[/dim green]")

    # 4. Global Persistent Memory
    mem_file = REDX_DIR / "memory.md"
    if mem_file.exists():
        mem_content = mem_file.read_text(errors="replace").strip()
        if mem_content and "[Persistent Memory]" not in sys_content:
            sys_content += f"\n\n[Persistent Memory — facts saved across all sessions:]\n{mem_content}"
            line_count = mem_content.count("\n") + 1
            console.print(f"[dim cyan]🧠 Memory loaded ({line_count} facts)[/dim cyan]")
        
    if session.messages:
        session.messages[0]["content"] = sys_content

    # Start heartbeat + model refresh daemon (background, every 60s)
    hb_thread = threading.Thread(target=run_heartbeat, args=(api_key, nv_key), daemon=True)
    hb_thread.start()

    load_mcp_servers()
    hooks     = load_hooks()
    debug     = [args.debug]
    auto_yes  = [args.yes]
    last_input= [""]

    # SIGINT → cancel stream, not exit
    def _sigint(sig, frame):
        if _cancel.is_set(): sys.exit(0)
        _cancel.set()
    signal.signal(signal.SIGINT, _sigint)

    console.print(Panel.fit(
        f"[bold green]🔴 RedX CLI v{VERSION}[/bold green]\n"
        f"Session: [cyan]{session.name}[/cyan] | "
        f"Model: [cyan]{session.models[0].split('/')[1] if session.models else '?'}[/cyan]\n"
        f"{'[green]🔓 PERMIT MODE[/green]  ' if auto_yes[0] else '[dim]🔒[/dim]  '}"
        f"Type [bold]/help[/bold] for commands. Ctrl+C cancels AI.",
        border_style="red"
    ))

    if SKILLS_DIR.exists():
        skill_count = sum(
            1 for d in SKILLS_DIR.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        )
        console.print(
            f"[dim green]🛡 {skill_count} Agent Skills available "
            f"— /skill list to browse, /pentest <target> to run[/dim green]"
        )

    # Stdin piping: echo "fix this" | redx
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            inp = (" ".join(args.prompt) + "\n" + piped).strip() if args.prompt else piped
            console.print(f"[bold cyan]You (piped):[/bold cyan] {inp[:120]}{'...' if len(inp)>120 else ''}")
            process_turn(session, inp, api_key, debug, auto_yes, hooks, last_input)
            session.save()
            return  # non-interactive, exit after response
    elif args.prompt:
        inp = " ".join(args.prompt)
        console.print(f"[bold cyan]You:[/bold cyan] {inp}")
        process_turn(session, inp, api_key, debug, auto_yes, hooks, last_input)
        last_input[0] = inp

    session_obj = PromptSession()
    while True:
        try:
            pct = session.ctx_pct
            bar_color = "red" if pct>=90 else "yellow" if pct>=70 else "green"
            user_input = session_obj.prompt(HTML(
                f"<b><ansicyan>You</ansicyan></b>"
                f"<ansi{bar_color}>[{pct}%]</ansi{bar_color}>"
                f"<b><ansicyan>: </ansicyan></b>"
            ))
        except EOFError: break
        except KeyboardInterrupt: continue

        user_input = user_input.strip()
        if not user_input: continue

        # !! re-run last
        if user_input == "!!":
            if last_input[0]:
                user_input = last_input[0]
                console.print(f"[dim]Re-running: {user_input}[/dim]")
            else:
                console.print("[dim]No previous input.[/dim]"); continue

        # legacy text commands
        if user_input.lower() in ("exit","quit"): break
        if user_input.lower() == "clear":
            user_input = "/clear"

        # slash commands
        if user_input.startswith("/"):
            handled, should_exit = handle_slash(user_input, session, api_key, debug, auto_yes, hooks)
            if should_exit: break
            if handled: continue

        last_input[0] = user_input
        process_turn(session, user_input, api_key, debug, auto_yes, hooks, last_input)

    session.save()
    console.print(f"[dim]Session saved → {session.path}[/dim]")

if __name__ == "__main__":
    main()
