"""
RedX Proxy v4.3 — FastAPI Migration
- Migrated from Flask to FastAPI + native async streaming
- SSRF protection: blocks internal/loopback URLs
- SSL: conditional bypass via BYPASS_SSL env var
- Credentials loaded from .env
"""
import asyncio, json, ssl, traceback, os, logging, random, socket, ipaddress, uuid
import aiohttp, re
from fastapi import FastAPI, Request, Response, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse
from typing import List
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from urllib.parse import urlparse, quote_plus
from bs4 import BeautifulSoup
from collections import defaultdict
from dotenv import load_dotenv
from knowledge_engine import engine

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None
    logging.warning("duckduckgo_search not installed — DDG search will be unavailable")

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8080")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Proxy Authentication ──────────────────────────────────────────────────────
PROXY_SECRET = os.getenv("PROXY_SECRET", "")
if not PROXY_SECRET:
    PROXY_SECRET = str(uuid.uuid4())
    logger.warning(f"[AUTH] No PROXY_SECRET in .env — auto-generated: {PROXY_SECRET}")
    logger.warning(f"[AUTH] Add PROXY_SECRET={PROXY_SECRET} to your .env file")
    # Auto-append to .env
    try:
        with open(".env", "a") as f:
            f.write(f"\nPROXY_SECRET={PROXY_SECRET}\n")
        logger.info("[AUTH] PROXY_SECRET auto-appended to .env")
    except Exception:
        pass

_AUTH_EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/api/proxy-token"}

class ProxyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)
        token = request.headers.get("X-Proxy-Token", "")
        if token != PROXY_SECRET:
            return JSONResponse(
                status_code=403,
                content={"error": {"message": "Unauthorized — invalid or missing proxy token"}},
            )
        return await call_next(request)

app.add_middleware(ProxyAuthMiddleware)

@app.get("/api/proxy-token")
async def get_proxy_token():
    """Returns the proxy token. Only accessible from localhost for initial setup."""
    return {"token": PROXY_SECRET}

OPENROUTER = "https://openrouter.ai/api/v1"

# ─── SSL Context ───────────────────────────────────────────────────────────────
def get_ssl_context():
    """Returns SSL context. Bypass only when BYPASS_SSL=true in .env"""
    if os.getenv("BYPASS_SSL", "false").lower() == "true":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        logger.warning("SSL verification BYPASSED (Fortinet mode)")
        return ctx
    return None  # aiohttp default: full verification


# ─── SSRF Protection ───────────────────────────────────────────────────────────
def is_safe_url(url: str) -> bool:
    """Blocks access to loopback, private, and link-local IP ranges."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            return False
        try:
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
                return False
        except socket.gaierror:
            pass  # DNS failure — let aiohttp handle it
        return True
    except Exception:
        return False


# ─── Vault Endpoints ───────────────────────────────────────────────────────────
@app.get("/vault")
async def get_vault():
    return engine.list_all()


@app.delete("/vault/{doc_id}")
async def delete_vault_item(doc_id: str):
    engine.delete_item(doc_id)
    return {"status": "deleted"}


@app.delete("/vault")
async def clear_vault():
    engine.clear_all()
    return {"status": "cleared"}


@app.get("/vault/search")
async def search_vault(q: str = "", n: int = 10):
    """Search the knowledge vault by semantic query."""
    if not q.strip():
        return []
    results = engine.search_knowledge(q, n_results=n)
    return [{"content": doc, "score": i} for i, doc in enumerate(results)]


@app.get("/vault/topics")
async def vault_topics():
    """Return distinct query topics stored in the knowledge vault."""
    items = engine.list_all()
    topics = set()
    for item in items:
        meta = item.get("metadata", {})
        query = meta.get("query", "")
        if query and query != "Knowledge Chunk":
            topics.add(query)
    return sorted(topics)


@app.post("/api/upload")
async def upload_file(request: Request, files: List[UploadFile] = File(...)):
    """Parse documents and vectorize them directly into ChromaDB."""
    auth = request.headers.get("Authorization", "")
    token = request.headers.get("X-Proxy-Token", "")
    if token != PROXY_SECRET:
        return JSONResponse(status_code=403, content={"error": "Invalid proxy token"})

    total_chunks = 0
    results = []

    for file in files:
        content = await file.read()
        text = ""
        filename = file.filename or "unknown"
        
        if filename.endswith(".pdf"):
            try:
                import pypdf
                import io
                pdf = pypdf.PdfReader(io.BytesIO(content))
                for page in pdf.pages:
                    text += page.extract_text() + "\n"
            except ImportError:
                return JSONResponse(status_code=500, content={"error": "pypdf package is missing. Run pip install pypdf."})
            except Exception as e:
                results.append({"filename": filename, "status": f"Failed: {str(e)}"})
                continue
        else:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                results.append({"filename": filename, "status": "Failed: Not a valid UTF-8 text file."})
                continue
        
        if not text.strip():
            results.append({"filename": filename, "status": "Empty file."})
            continue

        # Chunk logic
        chunk_size = 4000
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
        
        for idx, chunk in enumerate(chunks):
            await asyncio.to_thread(
                engine.add_knowledge, 
                chunk, 
                {"query": f"Document: {filename}", "type": "document", "chunk": idx}
            )
        
        total_chunks += len(chunks)
        results.append({"filename": filename, "status": f"Ingested {len(chunks)} chunks."})

    return {"message": f"Successfully vectorized {len(files)} files ({total_chunks} total chunks).", "details": results}


@app.get("/vault/graph")
async def vault_graph():
    """Return the knowledge graph as JSON nodes and edges for visualization."""
    nodes = []
    for node in engine.graph.nodes():
        nodes.append({"id": str(node), "label": str(node)})
    edges = []
    for u, v, data in engine.graph.edges(data=True):
        edges.append({
            "from": str(u),
            "to": str(v),
            "label": str(data.get("relation", "related_to")),
        })
    return {"nodes": nodes, "edges": edges}


# ─── URL Fetcher ───────────────────────────────────────────────────────────────
async def fetch_url_content(url: str) -> str:
    """Fetches real webpage content. Includes Deep Scrape for GitHub. SSRF-protected."""
    if not is_safe_url(url):
        logger.warning(f"SSRF blocked: {url}")
        return f"⛔ Access denied: URL '{url}' targets a restricted/internal address."

    ssl_ctx = get_ssl_context()
    connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}

    github_match = re.search(
        r"https?://github\.com/([^/]+)/([^/?#]+)(?:/tree/([^/]+)/(.*))?" , url
    )
    if github_match:
        owner  = github_match.group(1)
        repo   = github_match.group(2).replace(".git", "")
        branch = github_match.group(3) or "main"
        subpath = github_match.group(4) or ""
        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        output = f"--- ORIGINAL VERIFIED DATA ---\nGITHUB REPO: {owner}/{repo}\n"

        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args)) as session:
                async with session.get(tree_url) as resp:
                    if resp.status == 200:
                        tree_data = await resp.json()
                        all_items = tree_data.get("tree", [])
                        relevant = [i for i in all_items if i["path"].startswith(subpath)] if subpath else all_items

                        skills = defaultdict(list)
                        prefix = subpath + "/" if subpath else ""
                        for item in relevant:
                            rel_path = item["path"][len(prefix):] if prefix else item["path"]
                            parts = rel_path.split("/")
                            if len(parts) >= 1 and parts[0]:
                                skill_name = parts[0]
                                file_path = "/".join(parts[1:]) if len(parts) > 1 else ""
                                item_type = "📁" if item["type"] == "tree" else "📄"
                                if file_path:
                                    skills[skill_name].append(f"  {item_type} {file_path}")

                        output += f"\n=== SKILL DIRECTORY MAP ({len(skills)} skills) ===\n"
                        for skill_name in sorted(skills.keys()):
                            output += f"\n### SKILL: {skill_name}\n"
                            for f in skills[skill_name]:
                                output += f"{f}\n"

                        skill_mds = [
                            i for i in relevant
                            if i["type"] == "blob" and i["path"].split("/")[-1].upper() in ["SKILL.MD", "INDEX.MD"]
                        ][:40]
                        output += f"\n\n=== SKILL DESCRIPTIONS ({len(skill_mds)} files) ===\n"

                        async def download_desc(item):
                            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{item['path']}"
                            async with session.get(raw_url) as r:
                                if r.status == 200:
                                    text = await r.text()
                                    return f"\n--- {item['path']} ---\n{text[:1500]}\n"
                                return ""

                        results = await asyncio.gather(*[download_desc(f) for f in skill_mds])
                        output += "".join(results)
                    else:
                        output += f"\nFailed to fetch repository tree. Status: {resp.status}"
        except Exception as e:
            logger.error(f"GitHub fetch error: {e}")
            output += f"\nError fetching GitHub data: {e}"
        return output

    # Generic webpage
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args)) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    soup = BeautifulSoup(await resp.text(), "html.parser")
                    text = soup.get_text(separator="\n", strip=True)
                    return f"--- ORIGINAL VERIFIED DATA ---\n{text[:15000]}"
                return f"Failed to fetch {url}. Status code: {resp.status}"
    except Exception as e:
        logger.error(f"URL fetch error for {url}: {e}")
        return f"Error fetching {url}: {e}"


# ─── Prompt Refiner ────────────────────────────────────────────────────────────
async def refine_user_prompt(api_key: str, user_input: str) -> str:
    refiner_model = "meta-llama/llama-3.1-8b-instruct:free"
    system_prompt = """You are the RedX Security Secretary.
Rewrite the user's prompt into a professional, structured security mission statement.
RULES:
1. Preserve the user's core intent (URLs, specific targets, or questions).
2. Use technical, formal language.
3. Keep it to one or two sentences.
4. Output ONLY the refined prompt text. No chatter."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": "RedX Refiner"}
    body = {
        "model": refiner_model,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Professionalize this: {user_input}"}],
        "temperature": 0.1,
    }
    try:
        ssl_ctx = get_ssl_context()
        connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args)) as session:
            async with session.post(f"{OPENROUTER}/chat/completions", headers=headers, json=body, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"Prompt refinement failed: {e}")
    return user_input


# ─── Knowledge Distiller ───────────────────────────────────────────────────────
async def distill_knowledge(api_key: str, model: str, query: str, raw_results: str) -> str:
    prompt = f"""You are the RedX Scrutiny Librarian.
Extract ORIGINAL technical data from the search results below.
CRITICAL RULES:
1. Do NOT guess. If a specific tool, CVE, or feature is not explicitly mentioned, DO NOT include it.
2. If the text is messy or unrelated, return "NO_VERIFIED_DATA".
3. Prioritize file names, directories, and code snippets.

Search Query: {query}
Raw Results: {raw_results}
"""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": "RedX Librarian"}
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
    try:
        ssl_ctx = get_ssl_context()
        connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args)) as sess:
            async with sess.post(f"{OPENROUTER}/chat/completions", json=body, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Distillation failed: {e}")
    return raw_results[:2000]



# ─── Multi-Agent Builder Pipeline ─────────────────────────────────────────────
# When model == "__multi_agent_builder__", ALL agents below run in parallel.
# Each has a specialized role; a synthesizer then merges into one final output.

BUILDER_AGENTS = [
    {
        # Primary: Qwen3 Coder — best free coding model
        # Fallbacks: GPT-OSS 20B → Llama 3.3 70B → DeepSeek V4 (huge ctx)
        "model": "qwen/qwen3-coder:free",
        "fallbacks": [
            "openai/gpt-oss-20b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-v4-flash:free",
        ],
        "role": "🔧 Lead Code Engineer",
        "focus": (
            "Write the actual implementation code — functions, classes, algorithms, and logic. "
            "Focus on correctness, efficiency, and clean pythonic/idiomatic style. "
            "Include imports, type hints, and inline comments."
        ),
    },
    {
        # Primary: Laguna M.1 — specialized code/architecture model
        # Fallbacks: GPT-OSS 120B → Nemotron Super (1M ctx) → Qwen3 Next
        "model": "poolside/laguna-m.1:free",
        "fallbacks": [
            "openai/gpt-oss-120b:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "qwen/qwen3-next-80b-a3b-instruct:free",
        ],
        "role": "🏗️ Software Architect",
        "focus": (
            "Design the overall system architecture: component boundaries, data flow diagrams (as text), "
            "module structure, API contracts, and interface definitions. "
            "Prioritize scalability, maintainability, and separation of concerns."
        ),
    },
    {
        # Primary: GPT-OSS 120B — strong on docs and standards
        # Fallbacks: Laguna XS.2 → Hermes 3 405B → Llama 3.3 70B
        "model": "openai/gpt-oss-120b:free",
        "fallbacks": [
            "poolside/laguna-xs.2:free",
            "nousresearch/hermes-3-llama-3.1-405b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
        "role": "📚 Best Practices & Docs Expert",
        "focus": (
            "Define coding standards, docstrings (Google style), error handling patterns, "
            "security hardening, input validation, logging strategy, and usage examples. "
            "Write a full README snippet for the requested feature."
        ),
    },
    {
        # Primary: DeepSeek V4 Flash — 1M ctx, great for review
        # Fallbacks: GLM 4.5 Air → Nemotron Nano Omni → MiniMax M2.5
        "model": "deepseek/deepseek-v4-flash:free",
        "fallbacks": [
            "z-ai/glm-4.5-air:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "minimax/minimax-m2.5:free",
        ],
        "role": "🔍 Code Reviewer & Optimizer",
        "focus": (
            "Review the logic for bugs, race conditions, memory leaks, and inefficiencies. "
            "Suggest concrete performance improvements, edge case handling, and refactoring. "
            "Flag any security vulnerabilities in the approach."
        ),
    },
    {
        # Primary: Nemotron 3 Super — 1M ctx, great for system design
        # Fallbacks: Arcee Trinity Thinking → GPT-OSS 20B → Nemotron Nano 30B
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "fallbacks": [
            "arcee-ai/trinity-large-thinking:free",
            "openai/gpt-oss-20b:free",
            "nvidia/nemotron-3-nano-30b-a3b:free",
        ],
        "role": "⚙️ System Design & Integration Specialist",
        "focus": (
            "Define system requirements, dependency stack, configuration schema, environment variables, "
            "deployment strategy, CI/CD hooks, and integration points with external APIs or databases."
        ),
    },
]

BUILDER_SYNTHESIZER = "qwen/qwen3-coder:free"
BUILDER_SYNTHESIZER_FALLBACKS = [
    "openai/gpt-oss-120b:free",
    "deepseek/deepseek-v4-flash:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

# Rate-limited status codes that warrant a retry
_RETRY_STATUSES = {429, 503, 529, 502}
_AGENT_MAX_RETRIES = 3       # retries per individual model
_AGENT_RETRY_BASE  = 2.0     # seconds base for exponential backoff


async def run_builder_agent(api_key: str, agent: dict, messages: list) -> dict:
    """
    Run a specialist agent with full resilience:
      - Retries up to _AGENT_MAX_RETRIES times per model on 429/503/502/529
      - Falls back to agent['fallbacks'] if all retries on the primary fail
      - Returns the first successful response, tagged with which model responded
    """
    system_prompt = (
        f"You are the {agent['role']} in an elite multi-agent software development team.\n"
        f"YOUR SPECIFIC MANDATE: {agent['focus']}\n\n"
        "Be highly technical and concise. Output ONLY your specialized contribution — "
        "do NOT repeat the user request. Start your response with your role label."
    )
    agent_messages = [{"role": "system", "content": system_prompt}] + [
        m for m in messages if m.get("role") != "system"
    ]

    models_to_try = [agent["model"]] + agent.get("fallbacks", [])

    for model in models_to_try:
        for attempt in range(_AGENT_MAX_RETRIES):
            try:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": f"RedX Builder — {agent['role']}",
                }
                body = {
                    "model": model,
                    "messages": agent_messages,
                    "temperature": 0.25,
                    "max_tokens": 2048,
                }
                ssl_ctx = get_ssl_context()
                connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(**connector_args)
                ) as sess:
                    async with sess.post(
                        f"{OPENROUTER}/chat/completions",
                        json=body,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            # Guard against malformed response
                            choices = data.get("choices") or []
                            if choices and choices[0].get("message", {}).get("content"):
                                content = choices[0]["message"]["content"].strip()
                                if len(content) > 20:
                                    logger.info(
                                        f"[Builder] ✅ {agent['role']} succeeded "
                                        f"via {model} (attempt {attempt+1})"
                                    )
                                    return {
                                        "role": agent["role"],
                                        "model": model,
                                        "content": content,
                                    }
                                # Empty/too-short reply — treat as failure
                                logger.warning(
                                    f"[Builder] {agent['role']} returned empty content via {model}"
                                )
                            # Fall through to retry / next model
                        elif resp.status in _RETRY_STATUSES:
                            wait = (_AGENT_RETRY_BASE ** attempt) + random.uniform(0, 1.5)
                            logger.warning(
                                f"[Builder] {agent['role']} → {model} HTTP {resp.status}. "
                                f"Retrying in {wait:.1f}s (attempt {attempt+1}/{_AGENT_MAX_RETRIES})"
                            )
                            await asyncio.sleep(wait)
                            continue  # retry same model
                        else:
                            err = await resp.text()
                            logger.warning(
                                f"[Builder] {agent['role']} → {model} HTTP {resp.status}: {err[:200]}"
                            )
                            break  # non-retryable error — skip to next fallback model

            except asyncio.TimeoutError:
                logger.warning(
                    f"[Builder] {agent['role']} → {model} timed out (attempt {attempt+1})"
                )
                if attempt < _AGENT_MAX_RETRIES - 1:
                    await asyncio.sleep(2)
                    continue
                break  # try next fallback model

            except Exception as exc:
                logger.error(f"[Builder] {agent['role']} → {model} exception: {exc}")
                break  # try next fallback model

            break  # exit retry loop after non-429 failure

        # If we reach here the current model exhausted retries — try next fallback
        logger.info(f"[Builder] {agent['role']} switching fallback from {model}...")

    # All models and retries exhausted
    logger.error(f"[Builder] {agent['role']} FAILED on all models: {models_to_try}")
    return {
        "role": agent["role"],
        "model": "none",
        "content": None,   # None = clear failure signal for the stream
        "error": f"All models unavailable: {', '.join(models_to_try)}",
    }


async def multi_agent_builder_stream(api_key: str, messages: list):
    """
    Multi-Agent Builder pipeline:
      1. All BUILDER_AGENTS run in parallel (asyncio.gather)
      2. Synthesizer model merges all contributions into one production-ready output
      3. Result is streamed back to the frontend
    """
    n = len(BUILDER_AGENTS)
    yield ("data: " + json.dumps({"status": f"🛠️ Assembling builder team — {n} specialist agents launching sequentially..."}) + "\n\n").encode()

    # ── Phase 1: Sequential Agent Execution ───────────────────────────────────
    # Architect (1) -> Coder (0) -> Reviewer (3) -> Docs (2)
    ordered_agents = [BUILDER_AGENTS[1], BUILDER_AGENTS[0], BUILDER_AGENTS[3], BUILDER_AGENTS[2]]
    agent_outputs = []
    current_context = ""

    for agent in ordered_agents:
        agent_messages = list(messages)
        if current_context:
            agent_messages.append({
                "role": "user",
                "content": f"Here is the work from the previous specialists. Build upon it, review it, or use it for your task:\n\n{current_context}"
            })
            
        r = await run_builder_agent(api_key, agent, agent_messages)
        
        if isinstance(r, dict) and r.get("content") is not None:
            agent_outputs.append(r)
            model_used = r.get("model", "?")
            yield ("data: " + json.dumps({"status": "✅ " + r["role"] + " → " + model_used}) + "\n\n").encode()
            current_context += f"\n\n--- {r['role']} Output ---\n{r['content']}\n"
        elif isinstance(r, dict):
            err_info = r.get("error", "unavailable")
            yield ("data: " + json.dumps({"status": "⚠️ " + r["role"] + " failed — " + err_info}) + "\n\n").encode()

    if not agent_outputs:
        yield ("data: " + json.dumps({"error": {"message": "All builder agents failed. Check your API key and model availability."}}) + "\n\n").encode()
        return

    # ── Phase 2: Synthesis ─────────────────────────────────────────────────────
    yield ("data: " + json.dumps({"status": f"🔮 Synthesizing {len(agent_outputs)} agent outputs into final solution..."}) + "\n\n").encode()

    original_query = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            original_query = m.get("content", "")
            break

    agent_block = "\n\n".join(
        "=" * 60 + "\n" + r["role"] + " (model: " + r["model"] + ")\n" + "=" * 60 + "\n" + r["content"]
        for r in agent_outputs
    )

    synthesis_prompt = f"""You are the Master Builder AI — the final synthesizer of an elite multi-agent software development team.

ORIGINAL USER REQUEST:
{original_query}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPECIALIST AGENT CONTRIBUTIONS ({len(agent_outputs)} agents):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{agent_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

YOUR TASK — synthesize ALL contributions into ONE complete, production-ready deliverable:

1. **Architecture Overview** — merge the architect's design into a clean diagram/description
2. **Complete Implementation** — combine and reconcile the code engineer's work with reviewer fixes
3. **Configuration & Setup** — include the system specialist's dependency/env/deployment notes
4. **Best Practices Applied** — weave in the docs expert's error handling, logging, and security notes
5. **Usage Example** — show a working example

Rules:
- Resolve conflicts between agents by selecting the most correct/secure approach
- Output clean, professional markdown with syntax-highlighted code blocks
- Do NOT include agent labels or meta-commentary in the final output
- This must be a complete, copy-paste-ready solution"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "RedX Master Builder Synthesizer",
    }
    synth_body = {
        "model": BUILDER_SYNTHESIZER,
        "messages": [{"role": "user", "content": synthesis_prompt}],
        "stream": True,
        "temperature": 0.2,
        "frequency_penalty": 0.1,
        "max_tokens": 8192,
    }

    synth_models = [BUILDER_SYNTHESIZER] + BUILDER_SYNTHESIZER_FALLBACKS
    for synth_model in synth_models:
        synth_body["model"] = synth_model
        try:
            ssl_ctx = get_ssl_context()
            connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(**connector_args)
            ) as sess:
                async with sess.post(
                    f"{OPENROUTER}/chat/completions",
                    json=synth_body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    if resp.status == 200:
                        yield ("data: " + json.dumps({"status": ""}) + "\n\n").encode()  # clear status bar
                        async for chunk in resp.content.iter_chunked(1024):
                            if chunk:
                                yield chunk
                        return  # done
                    elif resp.status in _RETRY_STATUSES:
                        logger.warning(f"[Builder] Synthesizer {synth_model} HTTP {resp.status} — trying fallback")
                        continue  # try next synthesizer model
                    else:
                        err = await resp.text()
                        yield ("data: " + json.dumps({"error": {"message": f"Synthesizer {synth_model} HTTP {resp.status}: {err[:200]}"}}) + "\n\n").encode()
                        return
        except Exception as e:
            logger.error(f"[Builder] Synthesizer {synth_model} exception: {e}")
            continue  # try next synthesizer model

    yield ("data: " + json.dumps({"error": {"message": "All synthesizer models failed. Try again later."}}) + "\n\n").encode()


# ─── Web Search Engine (DDG → HTML Scrape fallback) ───────────────────────────
async def web_search(query: str, max_results: int = 5) -> str:
    """Resilient web search: tries DDG API first, then raw HTML scrape."""
    # Attempt 1: DuckDuckGo search API
    if DDGS:
        try:
            results = await asyncio.to_thread(
                lambda: list(DDGS().text(query, max_results=max_results))
            )
            if results:
                text = " ".join(
                    f"{r.get('title', '')}. {r.get('body', '')}" for r in results
                )
                logger.info(f"[Search] DDG API returned {len(results)} results")
                return text[:4000]
        except Exception as e:
            logger.warning(f"[Search] DDG API failed: {e}")

    # Attempt 2: Raw HTML scrape of DuckDuckGo
    try:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        ssl_ctx = get_ssl_context()
        connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(**connector_args)
        ) as sess:
            async with sess.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/130.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    snippets = []
                    for result in soup.select(".result__snippet")[:max_results]:
                        snippets.append(result.get_text(strip=True))
                    if snippets:
                        text = " ".join(snippets)
                        logger.info(f"[Search] HTML scrape returned {len(snippets)} snippets")
                        return text[:4000]
    except Exception as e:
        logger.warning(f"[Search] HTML scrape failed: {e}")

    logger.error("[Search] All search methods exhausted")
    return ""


# ─── Main Proxy Endpoint ───────────────────────────────────────────────────────
@app.api_route("/proxy/v1/{endpoint:path}", methods=["GET", "POST", "OPTIONS"])
async def proxy(request: Request, endpoint: str):
    if request.method == "OPTIONS":
        return Response(status_code=200)

    auth           = request.headers.get("Authorization", "")
    referer        = request.headers.get("HTTP-Referer", "http://localhost:8080")
    title          = request.headers.get("X-Title", "RedX Chatbot")
    strict_mode    = request.headers.get("X-Strict-Mode") == "true"
    refiner_active = request.headers.get("X-Prompt-Refiner") == "true"

    try:
        body = await request.json()
    except Exception:
        body = {}

    url    = f"{OPENROUTER}/{endpoint}"
    stream = body.get("stream", False)

    if endpoint == "chat/completions" and not body.get("no_agent", False):
        try:
            api_key    = auth.replace("Bearer ", "").strip()
            logger.info(f"[AUTH-DEBUG] API Key: '{api_key[:10]}...{api_key[-5:] if len(api_key) > 5 else api_key}' (length: {len(api_key)})")
            model_name = body.get("model", "meta-llama/llama-3.1-8b-instruct:free")
            messages   = body.get("messages", [])

            user_input = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    user_input = m.get("content", "")
                    break

            # ── Multi-Agent Builder Mode ──────────────────────────────────────
            if model_name == "__multi_agent_builder__":
                return StreamingResponse(
                    multi_agent_builder_stream(api_key, messages),
                    media_type="text/event-stream",
                )

            if stream:
                async def generate_fast_stream():
                    # 0. Prompt Refinement
                    if refiner_active:
                        yield f"data: {json.dumps({'status': '🪄 Refining security mission...'})}\n\n".encode()
                        refined_prompt = await refine_user_prompt(api_key, user_input)
                        yield f"data: {json.dumps({'refined_prompt': refined_prompt})}\n\n".encode()
                    else:
                        refined_prompt = user_input

                    # 1. Local Knowledge Retrieval
                    yield f"data: {json.dumps({'status': '🧠 Retrieving local memory...'})}\n\n".encode()
                    local_context = engine.search_knowledge(user_input, n_results=3)
                    local_text = "\n".join(local_context) if local_context else ""

                    # 2. Smart Retrieval (URL vs Search)
                    search_results = ""
                    url_match = re.search(r"https?://[^\s]+", user_input)

                    # URL Memory: scan history for GitHub URLs if none in current message
                    if not url_match:
                        for m in reversed(messages):
                            if m.get("role") == "user":
                                history_url = re.search(r"https?://github\.com/[^\s.,;!?]+", m.get("content", ""))
                                if history_url:
                                    url_match = history_url
                                    logger.info(f"[URL-MEMORY] Recalled GitHub URL: {url_match.group(0)}")
                                    break

                    if url_match:
                        target_url = url_match.group(0).rstrip(".,;!?")
                        yield f"data: {json.dumps({'status': f'📡 Accessing Original Source: {target_url[:40]}...'})}\n\n".encode()
                        try:
                            search_results = await fetch_url_content(target_url)
                        except Exception as e:
                            logger.error(f"URL access failed: {e}")
                    else:
                        yield f"data: {json.dumps({'status': f'🔍 Searching 2026 data for: {user_input[:50]}...'})}\n\n".encode()
                        try:
                            search_results = await web_search(f"2026 update: {user_input}")
                        except Exception as e:
                            logger.error(f"Search failed: {e}")

                    # 3. Knowledge Distillation & Ingestion
                    if search_results:
                        yield f"data: {json.dumps({'status': '📥 Ingesting new knowledge...'})}\n\n".encode()
                        try:
                            summary = await distill_knowledge(api_key, model_name, user_input, search_results)
                            engine.add_knowledge(summary, {"query": user_input})
                        except Exception as e:
                            logger.error(f"Knowledge ingestion failed: {e}")

                    # 4. Context Assembly
                    final_context = ""
                    if local_text:
                        final_context += f"\n--- LOCAL MEMORY ---\n{local_text}\n---------------------\n"
                        yield f"data: {json.dumps({'status': '✅ Local findings retrieved. Reasoning...'})}\n\n".encode()
                    if search_results:
                        final_context += f"\n--- 2026 LIVE WEB SEARCH ---\n{search_results}\n----------------------------\n"
                        yield f"data: {json.dumps({'status': '✅ Factual web context retrieved. Synthesizing...'})}\n\n".encode()
                    if not final_context:
                        yield f"data: {json.dumps({'status': '⚠️ No specific data found. Using core intelligence...'})}\n\n".encode()

                    search_context = final_context

                    # Log context to file
                    try:
                        log_path = os.path.join(os.path.dirname(__file__), "last_context.log")
                        with open(log_path, "w") as f:
                            f.write(f"STRICT_MODE: {strict_mode}\nCONTEXT:\n{search_context}\n")
                    except Exception as e:
                        logger.warning(f"Could not write context log: {e}")

                    # 5. Build Augmented Messages
                    if strict_mode:
                        system_injection = f"""### SCRUTINY ENGINE ACTIVE ###
You are now in **Strict Verification Mode**.
The following block contains ORIGINAL DATA retrieved from live 2026 sources and local memory.
1. Use the data below as your ONLY source for technical specifications.
2. If data below contradicts training data, the data below is the ABSOLUTE TRUTH.
3. If the answer is not in the block below, say "I cannot verify this detail from the provided sources."
4. Cite sources as [LOCAL] or [LIVE] where appropriate.
5. Format your response cleanly using markdown tables or numbered lists.

--- ORIGINAL VERIFIED DATA ---
{search_context}
------------------------------
"""
                    else:
                        system_injection = f"""### STANDARD REASONING ACTIVE ###
Below is contextual data retrieved from live sources and memory.
Use your internal knowledge, logical inference, and the context below to fully answer the question.
Format your response cleanly using markdown tables or numbered lists. Do NOT repeat information.

--- VERIFIED DATA CONTEXT ---
{search_context}
-----------------------------
"""
                    aug_msgs = []
                    has_sys = False
                    for m in messages:
                        mc = m.copy()
                        if mc.get("role") == "system":
                            mc["content"] += "\n\n" + system_injection
                            has_sys = True
                        if mc.get("role") == "user" and mc.get("content") == user_input:
                            mc["content"] = refined_prompt
                        aug_msgs.append(mc)
                    if not has_sys:
                        aug_msgs.insert(0, {"role": "system", "content": system_injection})
                    if strict_mode:
                        aug_msgs.append({"role": "system", "content": "FINAL WARNING: STRICT MODE. Do NOT guess. If not in verified data, say 'Information not found'."})

                    body["messages"]    = aug_msgs
                    body["temperature"] = 0.2 if strict_mode else 0.7
                    body["frequency_penalty"] = 0.2
                    if "max_tokens" not in body:
                        body["max_tokens"] = 16384
                    with open("debug_prompt.log", "w") as f:
                        f.write(json.dumps(body, indent=2))

                    fwd_headers = {
                        "Authorization": auth,
                        "Content-Type":  "application/json",
                        "HTTP-Referer":  referer,
                        "X-Title":       title,
                    }

                    # 6. Super-Retry Loop
                    yielded_content = False
                    for attempt in range(10):
                        ssl_ctx = get_ssl_context()
                        connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
                        connector = aiohttp.TCPConnector(force_close=True, **connector_args)
                        try:
                            async with aiohttp.ClientSession(connector=connector) as sess:
                                async with sess.post(url, json=body, headers=fwd_headers, timeout=120) as resp:
                                    if resp.status in (429, 503):
                                        wait = (2 ** attempt) + random.uniform(0, 1)
                                        yield f"data: {json.dumps({'status': f'⏳ Model busy. Retrying in {wait:.1f}s (Attempt {attempt+1}/10)...'})}\n\n".encode()
                                        await asyncio.sleep(wait)
                                        continue
                                    if resp.status != 200:
                                        err = await resp.text()
                                        try:
                                            err_json = json.loads(err)
                                            err_msg = err_json.get("error", {}).get("message", str(err_json))
                                        except Exception:
                                            err_msg = err[:200] + "..." if len(err) > 200 else err

                                        if "busy" in err_msg.lower() or "limit" in err_msg.lower():
                                            wait = (2 ** attempt) + random.uniform(0, 1)
                                            yield f"data: {json.dumps({'status': f'⏳ Model reported busy. Retrying in {wait:.1f}s...'})}\n\n".encode()
                                            await asyncio.sleep(wait)
                                            continue
                                        yield f"data: {json.dumps({'error': {'message': f'Provider Error HTTP {resp.status}: {err_msg}'}})}\n\n".encode()
                                        return
                                    yield f"data: {json.dumps({'status': ''})}\n\n".encode()
                                    async for chunk in resp.content.iter_chunked(1024):
                                        yielded_content = True
                                        yield chunk
                                    if search_context:
                                        yield f"data: {json.dumps({'raw_context': search_context})}\n\n".encode()
                                    return
                        except Exception as e:
                            if yielded_content:
                                yield f"data: {json.dumps({'error': {'message': f'Stream interrupted: {str(e)}'}})}\n\n".encode()
                                return
                            if attempt < 9:
                                yield f"data: {json.dumps({'status': '⚠️ Connection error. Retrying...'})}\n\n".encode()
                                await asyncio.sleep(1)
                                continue
                            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n".encode()
                            return
                            
                    yield f"data: {json.dumps({'error': {'message': 'Max retries (10) exceeded. The provider is overloaded.'}})}\n\n".encode()

                return StreamingResponse(generate_fast_stream(), media_type="text/event-stream")

            else:
                # Non-streaming
                fwd_headers = {"Authorization": auth, "Content-Type": "application/json", "HTTP-Referer": referer, "X-Title": title}
                ssl_ctx = get_ssl_context()
                connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True, **connector_args)) as sess:
                    async with sess.post(url, json=body, headers=fwd_headers, timeout=120) as resp:
                        raw = await resp.read()
                        ct  = resp.headers.get("Content-Type", "application/json")
                        return Response(raw, status_code=resp.status, media_type=ct)

        except Exception as e:
            logger.error(f"Proxy agent error: {traceback.format_exc()}")

    # ─── Standard Pass-through ────────────────────────────────────────────────
    fwd_headers = {"Authorization": auth, "Content-Type": "application/json", "HTTP-Referer": referer, "X-Title": title}
    ssl_ctx = get_ssl_context()
    connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}

    if stream:
        async def generate():
            connector = aiohttp.TCPConnector(force_close=True, **connector_args)
            async with aiohttp.ClientSession(connector=connector) as sess:
                async with sess.post(url, json=body, headers=fwd_headers, timeout=120) as resp:
                    if resp.status != 200:
                        txt = await resp.text()
                        yield f"data: {json.dumps({'error': {'message': txt[:300], 'status': resp.status}})}\n\n".encode()
                        return
                    async for chunk in resp.content.iter_chunked(1024):
                        yield chunk
        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        try:
            connector = aiohttp.TCPConnector(force_close=True, **connector_args)
            async with aiohttp.ClientSession(connector=connector) as sess:
                async with sess.post(url, json=body, headers=fwd_headers, timeout=120) as resp:
                    raw = await resp.read()
                    ct  = resp.headers.get("Content-Type", "application/json")
                    return Response(raw, status_code=resp.status, media_type=ct)
        except Exception as e:
            logger.error(f"Pass-through error: {e}")
            return Response(
                json.dumps({"error": {"message": str(e)}}),
                status_code=500,
                media_type="application/json",
            )


# ─── Health Check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    bypass = os.getenv("BYPASS_SSL", "false").lower() == "true"
    return {
        "status":     "ok",
        "ssl_bypass": bypass,
        "search":     "DDG + HTML Scrape",
        "version":    "v5.0-redx",
    }


@app.post("/api/validate-key")
async def validate_key(request: Request):
    """Test an OpenRouter API key by making a minimal completion request."""
    try:
        body = await request.json()
        api_key = body.get("key", "").strip()
        if not api_key:
            return JSONResponse(status_code=400, content={"valid": False, "error": "No key provided"})

        ssl_ctx = get_ssl_context()
        connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Minimal request to validate the key
        test_body = {
            "model": "openai/gpt-oss-20b:free",
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 1,
        }
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(**connector_args)
        ) as sess:
            async with sess.post(
                f"{OPENROUTER}/chat/completions",
                json=test_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return {"valid": True, "message": "API key is valid ✅"}
                elif resp.status == 401:
                    return {"valid": False, "error": "Invalid API key"}
                elif resp.status == 402:
                    return {"valid": False, "error": "API key has no credits"}
                else:
                    err = await resp.text()
                    return {"valid": False, "error": f"HTTP {resp.status}: {err[:200]}"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"valid": False, "error": str(e)})


@app.get("/api/token-usage")
async def token_usage(request: Request):
    """Fetch token usage info from OpenRouter for the user's API key."""
    auth = request.headers.get("Authorization", "")
    api_key = auth.replace("Bearer ", "").strip()
    if not api_key:
        return JSONResponse(status_code=400, content={"error": "No API key"})

    try:
        ssl_ctx = get_ssl_context()
        connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(**connector_args)
        ) as sess:
            async with sess.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                return JSONResponse(status_code=resp.status, content={"error": "Failed to fetch usage"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

