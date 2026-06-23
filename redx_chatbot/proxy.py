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
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from typing import List
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from urllib.parse import urlparse, quote_plus
from bs4 import BeautifulSoup
from collections import defaultdict
from dotenv import load_dotenv
from knowledge_engine import engine

from github_agent import multi_agent_github_stream as _github_stream
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "framework", "core"))
try:
    from orchestrator import Orchestrator
    orchestrator = Orchestrator(os.path.join(os.path.dirname(os.path.abspath(__file__)), "framework"))
    orchestrator.load_skills()
except Exception as e:
    logger.error(f"Failed to load Orchestrator: {e}")
    orchestrator = None

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
        logging.warning("Using deprecated duckduckgo_search package — run: pip install ddgs")
    except ImportError:
        DDGS = None
        logging.warning("No search package installed — run: pip install ddgs")

load_dotenv()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@app.on_event("startup")
async def startup_event():
    logger.info("Performing startup health-checks...")
    try:
        from llm_adapter import OpenRouterAdapter
        adapter = OpenRouterAdapter()
        adapter.prune_models()
    except Exception as e:
        logger.warning(f"Startup model pruning failed: {e}")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Proxy-Token", "X-Strict-Mode", "X-Prompt-Refiner", "HTTP-Referer", "X-Title"],
)

# ─── Proxy Authentication ──────────────────────────────────────────────────────
PROXY_SECRET = os.getenv("PROXY_SECRET", "")
if not PROXY_SECRET:
    PROXY_SECRET = str(uuid.uuid4())
    logger.warning(f"[AUTH] No PROXY_SECRET in .env — auto-generated: {PROXY_SECRET}")
    logger.warning(f"[AUTH] Add PROXY_SECRET={PROXY_SECRET} to your .env file")
    # Auto-append to .env safely
    import fcntl
    try:
        env_path = ".env"
        with open(env_path, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            content = f.read()
            if "PROXY_SECRET=" not in content:
                f.write(f"\nPROXY_SECRET={PROXY_SECRET}\n")
                logger.info("[AUTH] PROXY_SECRET auto-appended to .env")
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logger.error(f"[AUTH] Failed to append PROXY_SECRET: {e}")

_AUTH_EXEMPT_PATHS = {"/health", "/api/validate-key", "/"}

class ProxyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or path in _AUTH_EXEMPT_PATHS or path.startswith("/static/"):
            return await call_next(request)
        
        if path.startswith("/api/reports/"):
            token = request.query_params.get("token", "")
            if token != PROXY_SECRET:
                return JSONResponse(status_code=403, content={"error": {"message": "Unauthorized - invalid or missing proxy token"}})
            return await call_next(request)
            
        token = request.headers.get("X-Proxy-Token", "")
        if token != PROXY_SECRET:
            return JSONResponse(
                status_code=403,
                content={"error": {"message": "Unauthorized — invalid or missing proxy token"}},
            )
        return await call_next(request)

app.add_middleware(ProxyAuthMiddleware)
app.mount("/api/reports", StaticFiles(directory="engagements"), name="reports")

# F-001/F-003 fix: Serve frontend from static/ directory (not project root)
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

@app.get("/")
async def serve_index():
    """Serve index.html with the proxy token injected server-side (F-002 fix)."""
    index_path = os.path.join(_STATIC_DIR, "index.html")
    with open(index_path, "r") as f:
        html = f.read()
    html = html.replace("__PROXY_TOKEN__", PROXY_SECRET)
    return HTMLResponse(content=html)

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="frontend_static")

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
def is_safe_url(url: str) -> tuple[bool, str]:
    """Blocks access to loopback, private, link-local, and multicast IP ranges. Returns (is_safe, resolved_ip)."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False, ""
        if hostname.lower() in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"):
            return False, ""
        try:
            # Resolve all IPs for the hostname to prevent DNS rebinding or mapped IP bypasses
            addr_info = socket.getaddrinfo(hostname, None)
            resolved_ip = addr_info[0][4][0]  # Take the first resolved IP
            for res in addr_info:
                ip = res[4][0]
                ip_obj = ipaddress.ip_address(ip)
                if (ip_obj.is_private or ip_obj.is_loopback or 
                    ip_obj.is_link_local or ip_obj.is_multicast or 
                    str(ip_obj) == "169.254.169.254"):
                    return False, ""
            return True, resolved_ip
        except socket.gaierror:
            return False, ""
    except Exception:
        return False, ""


# ─── Vault Endpoints ───────────────────────────────────────────────────────────
@app.get("/vault")
async def get_vault():
    return await engine.list_all_async()


@app.delete("/vault/{doc_id}")
async def delete_vault_item(doc_id: str):
    await engine.delete_item_async(doc_id)
    return {"status": "deleted"}


@app.delete("/vault")
async def clear_vault():
    await asyncio.to_thread(engine.clear_all)
    return {"status": "cleared"}


@app.get("/vault/search")
async def search_vault(q: str = "", n: int = 10):
    """Search the knowledge vault by semantic query."""
    if not q.strip():
        return []
    results = await engine.search_knowledge_async(q, n_results=n)
    return [{"content": doc, "score": i} for i, doc in enumerate(results)]


@app.get("/vault/topics")
async def vault_topics():
    """Return distinct query topics stored in the knowledge vault."""
    items = await engine.list_all_async()
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
            await engine.add_knowledge_async(
                chunk, 
                {"query": f"Document: {filename}", "type": "document", "chunk": idx}
            )
        
        total_chunks += len(chunks)
        results.append({"filename": filename, "status": f"Ingested {len(chunks)} chunks."})

    return {"message": f"Successfully vectorized {len(files)} files ({total_chunks} total chunks).", "details": results}


@app.get("/vault/graph")
async def vault_graph():
    """Return the knowledge graph as JSON nodes and edges for visualization."""
    graph_data = await engine.get_graph_data_async()
    nodes = [{"id": str(node), "label": str(node)} for node in graph_data["nodes"]]
    edges = [{
        "from": str(u),
        "to": str(v),
        "label": str(data.get("relation", "related_to")),
    } for u, v, data in graph_data["edges"]]
    return {"nodes": nodes, "edges": edges}


# ─── URL Fetcher ───────────────────────────────────────────────────────────────
async def fetch_url_content(url: str) -> str:
    """Fetches real webpage content. Includes Deep Scrape for GitHub. SSRF-protected."""
    is_safe, resolved_ip = is_safe_url(url)
    if not is_safe:
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
        # CRITICAL FIX: Cap GitHub output to prevent context overflow
        if len(output) > 15000:
            output = output[:15000] + "\n\n[...Output truncated to fit model context window...]"
        return output

    # Generic webpage
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args)) as session:
            current_url = url
            from urllib.parse import urljoin
            for _ in range(5):  # Max 5 redirects
                is_safe, redirect_ip = is_safe_url(current_url)
                if not is_safe:
                    logger.warning(f"SSRF blocked during redirect: {current_url}")
                    return f"⛔ Access denied: URL '{current_url}' targets a restricted/internal address."
                
                parsed_cur = urlparse(current_url)
                safe_url = f"{parsed_cur.scheme}://{redirect_ip}{parsed_cur.path}"
                if parsed_cur.query: safe_url += f"?{parsed_cur.query}"
                headers = {"Host": parsed_cur.hostname}
                
                async with session.get(safe_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30), allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get('Location')
                        if not location:
                            break
                        current_url = urljoin(current_url, location)
                        continue

                    if resp.status == 200:
                        soup = BeautifulSoup(await resp.text(), "html.parser")
                        text = soup.get_text(separator="\n", strip=True)
                        return f"--- ORIGINAL VERIFIED DATA ---\n{text[:15000]}"
                    return f"Failed to fetch {url}. Status code: {resp.status}"
            return f"Failed to fetch {url}. Too many redirects."
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
async def distill_knowledge(api_key: str, query: str, raw_results: str) -> str:
    """
    Distills raw search results into clean intelligence.
    Tries a pool of fast free models with automatic fallback.
    Returns raw_results on total failure so knowledge is never lost.
    """
    prompt = f"""You are the RedX Scrutiny Librarian.
Extract ORIGINAL technical data from the search results below.
CRITICAL RULES:
1. Do NOT guess. If a specific tool, CVE, or feature is not explicitly mentioned, DO NOT include it.
2. If the text is messy or unrelated, return "NO_VERIFIED_DATA".
3. Prioritize file names, directories, and code snippets.

Search Query: {query}
Raw Results: {raw_results}
"""
    # Use fast lightweight models for distillation to avoid wasting rate limits
    distill_models = [
        "meta-llama/llama-3.2-3b-instruct:free",
        "nvidia/nemotron-nano-9b-v2:free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": "RedX Librarian"}
    for model in distill_models:
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 2048}
        try:
            ssl_ctx = get_ssl_context()
            connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args)) as sess:
                async with sess.post(f"{OPENROUTER}/chat/completions", json=body, headers=headers, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Check for embedded API errors
                        if "error" in data:
                            logger.warning(f"[Brain] Distillation model {model} returned error in body: {data['error']}, trying fallback")
                            continue
                        result = data["choices"][0]["message"]["content"].strip()
                        if result and result != "NO_VERIFIED_DATA" and len(result) >= 20:
                            logger.info(f"[Brain] Distillation succeeded via {model}")
                            return result
                        logger.warning(f"[Brain] Distillation model {model} returned empty/junk result, trying fallback")
                        continue  # Try next model
                    elif resp.status == 429:
                        logger.warning(f"[Brain] Distillation model {model} rate-limited, trying fallback")
                        continue  # Try next model
                    else:
                        err_text = await resp.text()
                        logger.warning(f"[Brain] Distillation model {model} HTTP {resp.status}: {err_text[:200]}, trying fallback")
                        continue
        except Exception as e:
            logger.warning(f"[Brain] Distillation via {model} failed: {e}")
            continue
    # All distillation models failed — store raw results directly
    logger.warning("[Brain] All distillation models failed, storing raw search data directly")
    return raw_results[:3000]



# ─── Multi-Agent Builder Pipeline ─────────────────────────────────────────────
# When model == "__multi_agent_builder__", ALL agents below run in parallel.
# Each has a specialized role; a synthesizer then merges into one final output.

BUILDER_AGENTS = [
    {
        "model": "qwen/qwen3-coder:free",
        "fallbacks": [
            "openai/gpt-oss-20b:free",
            "google/gemma-4-26b-a4b-it:free",
            "moonshotai/kimi-k2.6:free",
        ],
        "role": "🔧 Lead Code Engineer",
        "focus": (
            "Write the actual implementation code — functions, classes, algorithms, and logic. "
            "Focus on correctness, efficiency, and clean pythonic/idiomatic style. "
            "Include imports, type hints, and inline comments."
        ),
    },
    {
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
        "model": "moonshotai/kimi-k2.6:free",
        "fallbacks": [
            "z-ai/glm-4.5-air:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "moonshotai/kimi-k2.6:free",
        ],
        "role": "🔍 Code Reviewer & Optimizer",
        "focus": (
            "Review the logic for bugs, race conditions, memory leaks, and inefficiencies. "
            "Suggest concrete performance improvements, edge case handling, and refactoring. "
            "Flag any security vulnerabilities in the approach."
        ),
    },
    {
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "fallbacks": [
            "google/gemma-4-31b-it:free",
            "nvidia/nemotron-nano-9b-v2:free",
            "nvidia/nemotron-3-nano-30b-a3b:free",
        ],
        "role": "⚙️ System Design & Integration Specialist",
        "focus": (
            "Define system requirements, dependency stack, configuration schema, environment variables, "
            "deployment strategy, CI/CD hooks, and integration points with external APIs or databases."
        ),
    },
]

BUILDER_SYNTHESIZER = "poolside/laguna-m.1:free"
BUILDER_SYNTHESIZER_FALLBACKS = [
    "openai/gpt-oss-120b:free",
    "moonshotai/kimi-k2.6:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]

# Rate-limited status codes that warrant a retry
_RETRY_STATUSES = {429, 503, 529, 502}
_AGENT_MAX_RETRIES = 3       # retries per individual model
_AGENT_RETRY_BASE  = 2.0     # seconds base for exponential backoff


async def run_builder_agent(api_key: str, agent: dict, messages: list, max_tokens: int = 2048) -> dict:
    """
    Run a specialist agent with full resilience:
      - Retries up to _AGENT_MAX_RETRIES times per model on 429/503/502/529
      - Falls back to agent['fallbacks'] if all retries on the primary fail
      - Returns the first successful response, tagged with which model responded
    """
    custom_system = None
    for m in messages:
        if m.get("role") == "system":
            custom_system = m.get("content")
            break

    system_prompt = custom_system if custom_system else (
        f"You are the {agent['role']} in an elite multi-agent software development team.\n"
        f"YOUR SPECIFIC MANDATE: {agent['focus']}\n\n"
        "Be highly technical and concise. Output ONLY your specialized contribution — "
        "do NOT repeat the user request. Start your response with your role label."
    )
    
    agent_messages = [{"role": "system", "content": system_prompt}] + [
        m for m in messages if m.get("role") != "system"
    ]

    models_to_try = [agent["model"]] + agent.get("fallbacks", [])

    last_error_msg = "Unknown error"
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
                    "max_tokens": max_tokens,
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
                        timeout=aiohttp.ClientTimeout(total=600),
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
                                last_error_msg = "Empty content returned"
                            # Fall through to retry / next model
                        elif resp.status in _RETRY_STATUSES:
                            wait = (_AGENT_RETRY_BASE ** attempt) + random.uniform(0, 1.5)
                            last_error_msg = f"HTTP {resp.status} Rate Limit"
                            logger.warning(
                                f"[Builder] {agent['role']} → {model} HTTP {resp.status}. "
                                f"Retrying in {wait:.1f}s (attempt {attempt+1}/{_AGENT_MAX_RETRIES})"
                            )
                            await asyncio.sleep(wait)
                            continue  # retry same model
                        else:
                            err = await resp.text()
                            last_error_msg = f"HTTP {resp.status}: {err[:150]}"
                            logger.warning(
                                f"[Builder] {agent['role']} → {model} HTTP {resp.status}: {err[:200]}"
                            )
                            break  # non-retryable error — skip to next fallback model

            except asyncio.TimeoutError:
                last_error_msg = "TimeoutError"
                logger.warning(
                    f"[Builder] {agent['role']} → {model} timed out (attempt {attempt+1})"
                )
                if attempt < _AGENT_MAX_RETRIES - 1:
                    await asyncio.sleep(2)
                    continue
                break  # try next fallback model

            except Exception as exc:
                last_error_msg = f"Exception: {exc}"
                logger.error(f"[Builder] {agent['role']} → {model} exception: {exc}")
                break  # try next fallback model

            break  # exit retry loop after non-429 failure

        # If we reach here the current model exhausted retries — try next fallback
        logger.info(f"[Builder] {agent['role']} switching fallback from {model}...")

    # All models and retries exhausted
    logger.error(f"[Builder] {agent['role']} FAILED on all models: {models_to_try}. Last error: {last_error_msg}")
    return {
        "role": agent["role"],
        "model": "none",
        "content": None,   # None = clear failure signal for the stream
        "error": f"Failed (Last err: {last_error_msg})",
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
        "max_tokens": 16384,
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
                    timeout=aiohttp.ClientTimeout(total=600),
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


# ─── Category Multi-Agent Configs ─────────────────────────────────────────────

REDTEAM_AGENTS = [
    {"model": "moonshotai/kimi-k2.6:free", "fallbacks": ["moonshotai/kimi-k2.6:free", "nvidia/nemotron-3-nano-30b-a3b:free"], "role": "🔍 Recon Scout", "focus": "Enumerate the full attack surface: open ports, services, tech stack, subdomains, exposed endpoints, and misconfigs. Be exhaustive and technical."},
    {"model": "qwen/qwen3-coder:free", "fallbacks": ["poolside/laguna-m.1:free", "google/gemma-4-26b-a4b-it:free"], "role": "💥 Exploit Developer", "focus": "Write targeted exploit code or PoC for the identified vulnerabilities. Include shellcode, payloads, or scripts as appropriate. Fully functional code only."},
    {"model": "nvidia/nemotron-3-super-120b-a12b:free", "fallbacks": ["google/gemma-4-31b-it:free", "nousresearch/hermes-3-llama-3.1-405b:free"], "role": "🔑 Privilege Escalation Specialist", "focus": "Identify all privilege escalation paths: SUID binaries, sudo misconfigs, cron jobs, writable paths, kernel exploits, token impersonation. Give step-by-step commands."},
    {"model": "nousresearch/hermes-3-llama-3.1-405b:free", "fallbacks": ["cognitivecomputations/dolphin-mistral-24b-venice-edition:free", "z-ai/glm-4.5-air:free"], "role": "👻 Post-Exploitation Operator", "focus": "Define lateral movement, persistence mechanisms, credential harvesting, data exfil paths, and C2 communication strategy. Include MITRE ATT&CK technique IDs."},
    {"model": "openai/gpt-oss-120b:free", "fallbacks": ["poolside/laguna-m.1:free", "meta-llama/llama-3.3-70b-instruct:free"], "role": "📋 Pentest Report Writer", "focus": "Format all findings as a professional pentest report: Executive Summary, Risk Ratings (CVSS), Technical Details, Evidence, and Remediation Recommendations."},
]
REDTEAM_SYNTHESIZER = "nvidia/nemotron-3-super-120b-a12b:free"
REDTEAM_SYNTHESIZER_FALLBACKS = ["openai/gpt-oss-120b:free", "moonshotai/kimi-k2.6:free"]
REDTEAM_SYNTHESIS_PROMPT = """You are the Master Red Team AI synthesizing a full penetration test operation plan.
Merge all specialist contributions into ONE complete attack playbook:
1. **Attack Surface** — recon findings, target overview
2. **Exploit Chain** — working PoC code and step-by-step execution
3. **Privilege Escalation** — exact commands and paths
4. **Post-Exploitation** — persistence, lateral movement, exfil
5. **Report Summary** — CVSS-scored findings with remediation
Rules: Resolve conflicts using the most technically accurate approach. Output clean professional markdown with syntax-highlighted code blocks."""

EXPLOIT_AGENTS = [
    {"model": "moonshotai/kimi-k2.6:free", "fallbacks": ["nvidia/nemotron-3-super-120b-a12b:free", "moonshotai/kimi-k2.6:free"], "role": "🔬 Vulnerability Researcher", "focus": "Analyze the target for CVEs, logic flaws, injection points, and memory corruption. Map the root cause and attack surface precisely."},
    {"model": "qwen/qwen3-coder:free", "fallbacks": ["google/gemma-4-26b-a4b-it:free", "poolside/laguna-xs.2:free"], "role": "💻 PoC Coder", "focus": "Write a complete, working proof-of-concept exploit. Include all imports, payload setup, target connection, and execution logic. Must be functional."},
    {"model": "poolside/laguna-m.1:free", "fallbacks": ["openai/gpt-oss-20b:free", "poolside/laguna-xs.2:free"], "role": "🎯 Payload Crafter", "focus": "Generate custom payloads: shellcode, encoded strings, polyglots, format strings, or ROP chains as needed. Include encoding and obfuscation variants."},
    {"model": "google/gemma-4-31b-it:free", "fallbacks": ["nousresearch/hermes-3-llama-3.1-405b:free", "z-ai/glm-4.5-air:free"], "role": "🛡️ Bypass Engineer", "focus": "Design WAF bypass, AV/EDR evasion, and ASLR/DEP defeat strategies. Include specific techniques and tooling (e.g., Donut, Shikata, AMSI bypass)."},
    {"model": "google/gemma-4-26b-a4b-it:free", "fallbacks": ["qwen/qwen3-next-80b-a3b-instruct:free", "openai/gpt-oss-20b:free"], "role": "✅ Exploit Validator", "focus": "Review the PoC and payload for logic errors, null byte issues, and reliability. Fix bugs and confirm exploitability. Add error handling and reliability improvements."},
]
EXPLOIT_SYNTHESIZER = "qwen/qwen3-coder:free"
EXPLOIT_SYNTHESIZER_FALLBACKS = ["poolside/laguna-m.1:free", "openai/gpt-oss-120b:free"]
EXPLOIT_SYNTHESIS_PROMPT = """You are the Master Exploit AI synthesizing a complete weaponized exploit.
Merge all specialist contributions into ONE complete exploit deliverable:
1. **Vulnerability Analysis** — root cause, CVE references, CVSS score
2. **Complete Exploit Code** — fully working, syntax-highlighted, copy-paste ready
3. **Payload Variants** — encoded/obfuscated versions for different scenarios
4. **Bypass Techniques** — WAF/AV/EDR evasion integrated into the exploit
5. **Usage Instructions** — exact commands to run the exploit against target
Rules: The final code must actually work. Resolve any conflicts between agents. Output clean markdown with syntax-highlighted code blocks."""

CTF_AGENTS = [
    {"model": "google/gemma-4-31b-it:free", "fallbacks": ["nvidia/nemotron-3-super-120b-a12b:free", "z-ai/glm-4.5-air:free"], "role": "🧩 Challenge Analyst", "focus": "Identify the CTF challenge type (web, pwn, crypto, forensics, rev, misc). Describe the attack surface, entry point, and initial observations."},
    {"model": "nvidia/nemotron-3-super-120b-a12b:free", "fallbacks": ["google/gemma-4-31b-it:free", "moonshotai/kimi-k2.6:free"], "role": "🔐 Cryptography Breaker", "focus": "Analyze any cryptographic elements: weak keys, padding oracles, ECB mode, RSA small exponent, hash collisions. Provide working decryption code."},
    {"model": "qwen/qwen3-coder:free", "fallbacks": ["google/gemma-4-26b-a4b-it:free", "poolside/laguna-m.1:free"], "role": "🔧 Binary Reverser", "focus": "Analyze binaries, decompile with Ghidra/IDA pseudo-code, identify vulnerable functions, write exploit scripts (pwntools preferred). Patch binaries if needed."},
    {"model": "z-ai/glm-4.5-air:free", "fallbacks": ["nousresearch/hermes-3-llama-3.1-405b:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"], "role": "🔗 Logic Chainer", "focus": "Chain multiple vulnerabilities together. Multi-step reasoning for complex challenges. Identify the exact sequence of steps from entry to flag."},
    {"model": "nousresearch/hermes-3-llama-3.1-405b:free", "fallbacks": ["cognitivecomputations/dolphin-mistral-24b-venice-edition:free", "openai/gpt-oss-120b:free"], "role": "🏁 Flag Hunter", "focus": "Execute the final exploitation step. Write the script that extracts the flag. Include error handling and verify the flag format (e.g., FLAG{...}, HTB{...})."},
]
CTF_SYNTHESIZER = "google/gemma-4-31b-it:free"
CTF_SYNTHESIZER_FALLBACKS = ["nvidia/nemotron-3-super-120b-a12b:free", "openai/gpt-oss-120b:free"]
CTF_SYNTHESIS_PROMPT = """You are the Master CTF Solver AI synthesizing a complete challenge walkthrough.
Merge all specialist contributions into ONE complete CTF solution:
1. **Challenge Overview** — type, difficulty, initial analysis
2. **Step-by-Step Walkthrough** — numbered steps from start to flag
3. **All Scripts/Code** — complete exploit/solve scripts with syntax highlighting
4. **Cryptographic Breakdown** — if applicable, show the math/crypto attack
5. **Flag** — clearly highlighted at the end
Rules: Output a clean, beginner-readable walkthrough that a student could follow. Use markdown headers and code blocks."""

OSINT_AGENTS = [
    {"model": "moonshotai/kimi-k2.6:free", "fallbacks": ["moonshotai/kimi-k2.6:free", "nvidia/nemotron-3-nano-30b-a3b:free"], "role": "🎯 Target Profiler", "focus": "Build a complete target profile: full name, aliases, organization, role, location indicators, public email, phone. Use OSINT methodology (Maltego-style)."},
    {"model": "nvidia/nemotron-3-nano-30b-a3b:free", "fallbacks": ["google/gemma-4-31b-it:free", "moonshotai/kimi-k2.6:free"], "role": "🗺️ Infrastructure Mapper", "focus": "Map all digital infrastructure: domains, subdomains, IP ranges, ASN, cloud provider (AWS/Azure/GCP), CDN, open ports, certificates (crt.sh), DNS records."},
    {"model": "nousresearch/hermes-3-llama-3.1-405b:free", "fallbacks": ["cognitivecomputations/dolphin-mistral-24b-venice-edition:free", "z-ai/glm-4.5-air:free"], "role": "👤 Social Engineer", "focus": "Identify social media presence, LinkedIn data, leaked credentials (HaveIBeenPwned patterns), phishing vectors, pretexting scenarios, and social engineering attack paths."},
    {"model": "moonshotai/kimi-k2.6:free", "fallbacks": ["moonshotai/kimi-k2.6:free", "meta-llama/llama-3.3-70b-instruct:free"], "role": "📊 Data Aggregator", "focus": "Aggregate all gathered intel into structured data. Cross-reference sources. Build a timeline of digital activity. Identify patterns and anomalies."},
    {"model": "openai/gpt-oss-120b:free", "fallbacks": ["nvidia/nemotron-3-super-120b-a12b:free", "poolside/laguna-m.1:free"], "role": "🔍 Intel Analyst", "focus": "Map threat actor TTPs, assess risk level, identify most viable attack vectors, build an attack narrative, and provide actionable intelligence recommendations."},
]
OSINT_SYNTHESIZER = "moonshotai/kimi-k2.6:free"
OSINT_SYNTHESIZER_FALLBACKS = ["openai/gpt-oss-120b:free", "nvidia/nemotron-3-super-120b-a12b:free"]
OSINT_SYNTHESIS_PROMPT = """You are the Master OSINT Analyst AI synthesizing a complete intelligence dossier.
Merge all specialist contributions into ONE structured intelligence report:
1. **Executive Summary** — who/what the target is and top-level risk
2. **Target Profile** — identity, roles, affiliations
3. **Infrastructure Map** — domains, IPs, cloud assets (table format)
4. **Social & Leaked Data** — social media, credentials, phishing vectors
5. **Attack Vectors** — prioritized list of viable attack paths
6. **OSINT Methodology** — tools and queries used for reproducibility
Rules: Format as a professional threat intelligence report. Use markdown tables for structured data."""

OFFENSIVE_AGENTS = [
    {"model": "nvidia/nemotron-3-super-120b-a12b:free", "fallbacks": ["google/gemma-4-31b-it:free", "openai/gpt-oss-120b:free"], "role": "📋 Attack Planner", "focus": "Design a full MITRE ATT&CK mapped attack plan. Define phases (Initial Access, Execution, Persistence, PrivEsc, Lateral Movement, Collection, Exfil). Reference exact technique IDs."},
    {"model": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free", "fallbacks": ["nousresearch/hermes-3-llama-3.1-405b:free", "poolside/laguna-m.1:free"], "role": "💣 Payload Specialist", "focus": "Create custom weaponized payloads for the attack plan. Include staged/stageless options, encoding, obfuscation, and delivery mechanism (phishing, USB, watering hole)."},
    {"model": "nousresearch/hermes-3-llama-3.1-405b:free", "fallbacks": ["z-ai/glm-4.5-air:free", "nvidia/nemotron-3-nano-30b-a3b:free"], "role": "👁️ Evasion Expert", "focus": "Design full OPSEC plan: AV/EDR evasion, log wiping, timestomping, living-off-the-land binaries (LOLBins), traffic blending, and anti-forensics techniques."},
    {"model": "google/gemma-4-31b-it:free", "fallbacks": ["nvidia/nemotron-3-super-120b-a12b:free", "moonshotai/kimi-k2.6:free"], "role": "📡 C2 Strategist", "focus": "Design C2 infrastructure: redirectors, domain fronting, beaconing intervals, encrypted channels (DNS/HTTPS), failover, and operator security practices."},
    {"model": "openai/gpt-oss-120b:free", "fallbacks": ["poolside/laguna-m.1:free", "meta-llama/llama-3.3-70b-instruct:free"], "role": "📝 Operation Debriefer", "focus": "Write post-operation summary: what worked, what failed, cleanup steps, artifacts left behind, lessons learned, and recommendations for future operations."},
]
OFFENSIVE_SYNTHESIZER = "nvidia/nemotron-3-super-120b-a12b:free"
OFFENSIVE_SYNTHESIZER_FALLBACKS = ["openai/gpt-oss-120b:free", "google/gemma-4-31b-it:free"]
OFFENSIVE_SYNTHESIS_PROMPT = """You are the Master Offensive Operations AI synthesizing a complete red team operation playbook.
Merge all specialist contributions into ONE complete operational playbook:
1. **Operation Overview** — objectives, scope, rules of engagement
2. **MITRE ATT&CK Map** — technique IDs for each phase (table format)
3. **Payload Arsenal** — all payloads with usage instructions
4. **OPSEC Checklist** — step-by-step evasion and anti-forensics
5. **C2 Infrastructure** — setup guide with commands
6. **Post-Op Report** — cleanup, lessons learned
Rules: Professional red team format. All commands must be specific and executable."""

AUTOMATION_AGENTS = [
    {"model": "meta-llama/llama-3.3-70b-instruct:free", "fallbacks": ["moonshotai/kimi-k2.6:free", "nvidia/nemotron-nano-9b-v2:free"], "role": "✏️ Script Writer", "focus": "Write the core script logic: main functions, argument parsing, core algorithm. Use clean, idiomatic style for the appropriate language (Python/Bash/PowerShell)."},
    {"model": "moonshotai/kimi-k2.6:free", "fallbacks": ["meta-llama/llama-3.3-70b-instruct:free", "nvidia/nemotron-3-nano-30b-a3b:free"], "role": "🔄 Pipeline Engineer", "focus": "Design the full automation pipeline: input/output chaining, subprocess calls, parallelism, queue management, and integration with external APIs or tools."},
    {"model": "google/gemma-4-26b-a4b-it:free", "fallbacks": ["qwen/qwen3-next-80b-a3b-instruct:free", "openai/gpt-oss-20b:free"], "role": "🛡️ Error Handler", "focus": "Add comprehensive error handling: try/except blocks, input validation, timeout handling, retry logic, graceful degradation, and meaningful error messages."},
    {"model": "nvidia/nemotron-nano-9b-v2:free", "fallbacks": ["meta-llama/llama-3.2-3b-instruct:free", "poolside/laguna-xs.2:free"], "role": "⚡ Performance Optimizer", "focus": "Optimize for speed and efficiency: async/concurrent execution, caching, memory management, algorithmic improvements, and profiling recommendations."},
    {"model": "meta-llama/llama-3.2-3b-instruct:free", "fallbacks": ["nvidia/nemotron-nano-9b-v2:free", "meta-llama/llama-3.3-70b-instruct:free"], "role": "🚀 Deployment Specialist", "focus": "Write deployment configuration: systemd service files, cron jobs, Docker/Compose setup, CLI packaging (argparse/click), and installation instructions."},
]
AUTOMATION_SYNTHESIZER = "meta-llama/llama-3.3-70b-instruct:free"
AUTOMATION_SYNTHESIZER_FALLBACKS = ["moonshotai/kimi-k2.6:free", "openai/gpt-oss-120b:free"]
AUTOMATION_SYNTHESIS_PROMPT = """You are the Master Automation AI synthesizing a complete, production-ready automation solution.
Merge all specialist contributions into ONE complete automation deliverable:
1. **Overview** — what the script does, use case, supported platforms
2. **Complete Script** — fully working, syntax-highlighted, copy-paste ready
3. **Pipeline Design** — how data flows through the automation
4. **Error Handling** — all edge cases covered
5. **Deployment** — installation, cron/systemd setup, packaging
6. **Usage Examples** — exact commands with example output
Rules: Output must be immediately usable. All code must be complete and correct."""


# ─── Generic Multi-Agent Stream (shared by all category pipelines) ─────────────
async def multi_agent_generic_stream(api_key: str, messages: list, agents: list,
                                     synthesizer_model: str, synthesizer_fallbacks: list,
                                     synthesis_prompt_template: str, mode_label: str):
    """
    Generic multi-agent pipeline reused by all category modes.
    Agents run sequentially with context passing, then a synthesizer merges outputs.
    """
    n = len(agents)
    yield ("data: " + json.dumps({"status": f"🤖 {mode_label} — Phase 1: {n} independent specialists launching in parallel..."}) + "\n\n").encode()

    async def run_agent_phase1(agent):
        agent_messages = list(messages)
        # Pass max_tokens to ensure long responses aren't cut
        return await run_builder_agent(api_key, agent, agent_messages, max_tokens=8192)

    phase1_results = await asyncio.gather(*[run_agent_phase1(a) for a in agents])
    
    agent_outputs = []
    for i, r in enumerate(phase1_results):
        agent = agents[i]
        if isinstance(r, dict) and r.get("content"):
            agent_outputs.append(r)
            yield ("data: " + json.dumps({"status": f"✅ Phase 1: {r['role']} (Draft) → {r.get('model','?')}"}) + "\n\n").encode()
        else:
            err = r.get("error", "unavailable") if isinstance(r, dict) else "failed"
            yield ("data: " + json.dumps({"status": f"⚠️ Phase 1: {agent['role']} failed — {err}"}) + "\n\n").encode()

    if not agent_outputs:
        yield ("data: " + json.dumps({"error": {"message": f"All {mode_label} agents failed Phase 1. Check API key and model availability."}}) + "\n\n").encode()
        return

    # Phase 2: Peer Review & Debate
    yield ("data: " + json.dumps({"status": f"🗣️ Phase 2: Broadcasting drafts for parallel peer review & debate..."}) + "\n\n").encode()

    draft_block = "\n\n".join(
        "=" * 60 + f"\n{r['role']} (Draft)\n" + "=" * 60 + f"\n{r['content']}"
        for r in agent_outputs
    )

    async def run_agent_phase2(agent):
        agent_messages = list(messages)
        agent_messages.append({
            "role": "user",
            "content": f"PEER REVIEW PHASE.\nHere are the independent drafts generated by your team:\n\n{draft_block}\n\nReview their work, identify any contradictions or errors, and provide your FINAL corrected analysis based on your specialized mandate."
        })
        return await run_builder_agent(api_key, agent, agent_messages, max_tokens=8192)

    phase2_results = await asyncio.gather(*[run_agent_phase2(a) for a in agents])
    
    final_outputs = []
    for i, r in enumerate(phase2_results):
        agent = agents[i]
        if isinstance(r, dict) and r.get("content"):
            final_outputs.append(r)
            yield ("data: " + json.dumps({"status": f"🎯 Phase 2: {r['role']} (Final) → {r.get('model','?')}"}) + "\n\n").encode()

    if not final_outputs:
        final_outputs = agent_outputs

    yield ("data: " + json.dumps({"status": f"🔮 Phase 3: Synthesizing {len(final_outputs)} final debated analyses into report..."}) + "\n\n").encode()

    original_query = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    agent_block = "\n\n".join(
        "=" * 60 + f"\n{r['role']} (model: {r['model']})\n" + "=" * 60 + f"\n{r['content']}"
        for r in final_outputs
    )
    full_synthesis_prompt = (
        f"ORIGINAL USER REQUEST:\n{original_query}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"SPECIALIST CONTRIBUTIONS ({len(final_outputs)} agents after debate):\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{agent_block}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{synthesis_prompt_template}"
    )

    synth_models = [synthesizer_model] + synthesizer_fallbacks
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": f"RedX {mode_label} Synthesizer"}
    synth_body = {"model": synthesizer_model, "messages": [{"role": "user", "content": full_synthesis_prompt}],
                  "stream": True, "temperature": 0.2, "frequency_penalty": 0.1, "max_tokens": 16384}

    for synth_model in synth_models:
        synth_body["model"] = synth_model
        try:
            ssl_ctx = get_ssl_context()
            connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args)) as sess:
                async with sess.post(f"{OPENROUTER}/chat/completions", json=synth_body,
                                     headers=headers, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                    if resp.status == 200:
                        yield ("data: " + json.dumps({"status": ""}) + "\n\n").encode()
                        async for chunk in resp.content.iter_chunked(1024):
                            if chunk:
                                yield chunk
                        return
                    elif resp.status in _RETRY_STATUSES:
                        logger.warning(f"[{mode_label}] Synthesizer {synth_model} HTTP {resp.status} — trying fallback")
                        continue
                    else:
                        err = await resp.text()
                        yield ("data: " + json.dumps({"error": {"message": f"Synthesizer HTTP {resp.status}: {err[:200]}"}}) + "\n\n").encode()
                        return
        except Exception as e:
            logger.error(f"[{mode_label}] Synthesizer {synth_model} exception: {e}")
            continue

    yield ("data: " + json.dumps({"error": {"message": f"{mode_label}: All synthesizer models failed."}}) + "\n\n").encode()




# ─── Web Search Engine (DDG → HTML Scrape fallback) ───────────────────────────
async def web_search(query: str, max_results: int = 5) -> str:
    """Resilient web search: tries DDG API first, then raw HTML scrape."""
    logger.info(f"[Search] Starting search for: {query[:80]}")

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
            else:
                logger.warning("[Search] DDG API returned 0 results — falling through to HTML scrape")
        except Exception as e:
            logger.warning(f"[Search] DDG API failed: {e}")
    else:
        logger.warning("[Search] DDGS not available, skipping to HTML scrape")

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
                    else:
                        logger.warning(f"[Search] HTML scrape got 200 but no .result__snippet elements")
                else:
                    logger.warning(f"[Search] HTML scrape returned HTTP {resp.status}")
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
            model_name = body.get("model", "meta-llama/llama-3.1-8b-instruct:free")
            messages   = body.get("messages", [])

            user_input = ""
            has_images = False
            for m in reversed(messages):
                if m.get("role") == "user":
                    content = m.get("content", "")
                    # Handle multimodal content (array of parts)
                    if isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    user_input = part.get("text", "")
                                elif part.get("type") == "image_url":
                                    has_images = True
                    else:
                        user_input = content
                    break

            # Auto-switch to vision model if user sends images and current model doesn't support it
            VISION_MODELS = {
                "nvidia/nemotron-nano-12b-v2-vl:free",
                "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                "moonshotai/kimi-k2.6:free",
                "google/gemma-4-26b-a4b-it:free",
                "google/gemma-4-31b-it:free",
            }
            if has_images and model_name not in VISION_MODELS:
                logger.info(f"[Vision] Image detected but model '{model_name}' may not support vision. Auto-switching to nemotron-nano-12b-v2-vl:free")
                model_name = "nvidia/nemotron-nano-12b-v2-vl:free"
                body["model"] = model_name

            fallback_models = []
            if model_name == "__deep_research_mode__":
                model_name = "moonshotai/kimi-k2.6:free"
                body["model"] = model_name
                fallback_models = [
                    "google/gemma-4-31b-it:free",
                    "openai/gpt-oss-120b:free",
                    "meta-llama/llama-3.3-70b-instruct:free",
                    "qwen/qwen3-coder:free"
                ]

            if model_name == "__multi_agent_custom__":
                from starlette.responses import JSONResponse
                custom_agents_ids = body.get("custom_agents", [])
                if not custom_agents_ids:
                    return JSONResponse(status_code=400, content={"error": {"message": "No custom agents selected."}})
                
                custom_agents_list = []
                for i, aid in enumerate(custom_agents_ids[:5]):
                    custom_agents_list.append({
                        "model": aid,
                        "fallbacks": [],
                        "role": f"Specialist Agent {i+1}",
                        "focus": "Analyze the prompt from your unique perspective and contribute to the final solution."
                    })
                
                CUSTOM_SYNTHESIZER = "qwen/qwen3-coder:free"
                CUSTOM_SYNTHESIZER_FALLBACKS = ["openai/gpt-oss-120b:free"]
                CUSTOM_SYNTH_PROMPT = "You are the Custom Synthesizer AI. Merge all specialist contributions into one coherent, high-quality response. Ensure no details are lost."
                
                return StreamingResponse(
                    multi_agent_generic_stream(api_key, messages, custom_agents_list, CUSTOM_SYNTHESIZER, CUSTOM_SYNTHESIZER_FALLBACKS, CUSTOM_SYNTH_PROMPT, "⚙️ Custom Multi-Agent"),
                    media_type="text/event-stream",
                )
            # ── Multi-Agent Category Dispatch Router ──────────────────────────
            _MULTI_AGENT_ROUTES = {
                "__multi_agent_builder__":   (BUILDER_AGENTS,    BUILDER_SYNTHESIZER,    BUILDER_SYNTHESIZER_FALLBACKS,    "You are the Master Builder AI. Merge all specialist contributions into ONE complete, production-ready deliverable with Architecture, Code, Config, Best Practices, and Usage Example sections. Output clean professional markdown.", "🛠️ Tool & Software Builder"),
                "__multi_agent_redteam__":   (REDTEAM_AGENTS,    REDTEAM_SYNTHESIZER,    REDTEAM_SYNTHESIZER_FALLBACKS,    REDTEAM_SYNTHESIS_PROMPT,    "🔥 Red Team & PT"),
                "__multi_agent_exploit__":   (EXPLOIT_AGENTS,    EXPLOIT_SYNTHESIZER,    EXPLOIT_SYNTHESIZER_FALLBACKS,    EXPLOIT_SYNTHESIS_PROMPT,    "💻 Exploit Dev"),
                "__multi_agent_ctf__":       (CTF_AGENTS,        CTF_SYNTHESIZER,        CTF_SYNTHESIZER_FALLBACKS,        CTF_SYNTHESIS_PROMPT,        "🧠 CTF Solver"),
                "__multi_agent_osint__":     (OSINT_AGENTS,      OSINT_SYNTHESIZER,      OSINT_SYNTHESIZER_FALLBACKS,      OSINT_SYNTHESIS_PROMPT,      "🌐 OSINT & Recon"),
                "__multi_agent_offensive__": (OFFENSIVE_AGENTS,  OFFENSIVE_SYNTHESIZER,  OFFENSIVE_SYNTHESIZER_FALLBACKS,  OFFENSIVE_SYNTHESIS_PROMPT,  "🔓 Offensive Ops"),
                "__multi_agent_automation__":(AUTOMATION_AGENTS, AUTOMATION_SYNTHESIZER, AUTOMATION_SYNTHESIZER_FALLBACKS, AUTOMATION_SYNTHESIS_PROMPT, "⚡ Automation"),
            }
            if model_name in _MULTI_AGENT_ROUTES:
                agents, synth, synth_fallbacks, synth_prompt, label = _MULTI_AGENT_ROUTES[model_name]
                if model_name == "__multi_agent_builder__":
                    return StreamingResponse(multi_agent_builder_stream(api_key, messages), media_type="text/event-stream")
                return StreamingResponse(
                    multi_agent_generic_stream(api_key, messages, agents, synth, synth_fallbacks, synth_prompt, label),
                    media_type="text/event-stream",
                )

            if model_name == "__multi_agent_github__":
                return StreamingResponse(
                    _github_stream(api_key, messages, run_builder_agent, get_ssl_context),
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
                    local_context = await engine.search_knowledge_async(user_input, n_results=3)
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
                            logger.info(f"[Search] URL fetch returned {len(search_results)} chars")
                        except Exception as e:
                            logger.error(f"URL access failed: {e}")
                    else:
                        yield f"data: {json.dumps({'status': f'🔍 Searching 2026 data for: {user_input[:50]}...'})}\n\n".encode()
                        try:
                            search_results = await web_search(f"2026 update: {user_input}")
                            logger.info(f"[Search] web_search returned {len(search_results)} chars")
                        except Exception as e:
                            logger.error(f"Search failed: {e}")

                    logger.info(f"[Pipeline] search_results length: {len(search_results)}, user_input: {user_input[:60]}")

                    # 3. Knowledge Distillation & Ingestion
                    if search_results:
                        yield f"data: {json.dumps({'status': '📥 Ingesting new knowledge...'})}\n\n".encode()
                        try:
                            summary = await distill_knowledge(api_key, user_input, search_results)
                            if summary and len(summary.strip()) > 20:
                                stored = await engine.add_knowledge_async(summary, {"query": user_input})
                                if stored:
                                    logger.info(f"[Brain] ✅ Knowledge ingested for query: {user_input[:60]}")
                                    yield f"data: {json.dumps({'status': '🧠 Knowledge saved to Secondary Brain!'})}\n\n".encode()
                                else:
                                    logger.info(f"[Brain] ℹ️ Knowledge already exists (deduplication skipped).")
                            else:
                                # Last resort: store raw search results directly
                                await engine.add_knowledge_async(search_results[:3000], {"query": user_input, "type": "raw"})
                                logger.warning(f"[Brain] ⚠️ Stored raw search results as fallback")
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
                    # CRITICAL FIX: Hard-cap context size to prevent context window overflow
                    # CoBuddy/most free models have 131K token limit (~500K chars). Cap at 30K chars safely.
                    if len(search_context) > 30000:
                        search_context = search_context[:30000] + "\n\n[...Context truncated to fit model context window. Full data stored in Secondary Brain...]"
                        logger.warning(f"[Pipeline] Context truncated from {len(final_context)} to 30000 chars to prevent overflow")

                    # Log context to file
                    if os.getenv("DEBUG_LOG") == "true":
                        try:
                            log_path = os.path.join(os.path.dirname(__file__), "last_context.log")
                            with open(log_path, "w") as f:
                                f.write(f"STRICT_MODE: {strict_mode}\nCONTEXT:\n{search_context}\n")
                        except Exception as e:
                            logger.warning(f"Could not write context log: {e}")

                    # 5. Build Augmented Messages
                    # Detect if search_context contains real fetched data
                    has_live_data = bool(search_context and len(search_context.strip()) > 50)
                    data_source_note = "web content that was automatically fetched and pre-loaded by the RedX proxy server" if has_live_data else "your internal knowledge"

                    if strict_mode:
                        system_injection = f"""### REDX SCRUTINY ENGINE — STRICT MODE ###

CRITICAL INSTRUCTION: The RedX proxy server has ALREADY fetched all required web pages, URLs, and GitHub repositories on your behalf. The raw content is embedded directly below in the [FETCHED CONTENT] block. You DO NOT need to access the internet — the data is already here.

DO NOT say "I cannot access URLs" or "I cannot browse the web". You already have the data. Analyze and respond using it.

RULES:
1. The [FETCHED CONTENT] block below is the GROUND TRUTH. Treat it as if you read it yourself.
2. If the data contradicts your training knowledge, the [FETCHED CONTENT] data wins.
3. If the answer is genuinely not in the fetched data, say: "This detail was not found in the fetched source."
4. Cite as [FETCHED] or [LOCAL MEMORY] where appropriate.
5. Use markdown tables or numbered lists for clarity.

[FETCHED CONTENT]
{search_context}
[END FETCHED CONTENT]
"""
                    else:
                        system_injection = f"""### REDX INTELLIGENCE ENGINE — STANDARD MODE ###

CRITICAL INSTRUCTION: The RedX proxy server has ALREADY fetched all required web pages, URLs, and GitHub repositories on your behalf. The content is embedded in the [FETCHED CONTENT] block below. You DO NOT need to access the internet — the data is already here.

DO NOT say "I cannot access URLs" or "I cannot browse the web". The data has been pre-loaded for you.

INSTRUCTIONS:
1. Use the [FETCHED CONTENT] as your primary source, supplemented by your own knowledge.
2. Provide a thorough, technically detailed response.
3. Format with markdown headers, tables, and code blocks where appropriate.
4. Do NOT repeat the raw fetched content verbatim — synthesize and explain it.

[FETCHED CONTENT]
{search_context}
[END FETCHED CONTENT]
"""


                    aug_msgs = []
                    has_sys = False
                    for m in messages:
                        mc = m.copy()
                        if mc.get("role") == "system":
                            mc["content"] += "\n\n" + system_injection
                            has_sys = True
                        if mc.get("role") == "user":
                            content = mc.get("content", "")
                            # Handle multimodal content: only replace the text part
                            if isinstance(content, list):
                                text_parts = [p for p in content if isinstance(p, dict) and p.get("type") == "text"]
                                if text_parts and text_parts[-1].get("text") == user_input:
                                    text_parts[-1]["text"] = refined_prompt
                            elif content == user_input:
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
                        body["max_tokens"] = 8192
                    if os.getenv("DEBUG_LOG") == "true":
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
                                        if fallback_models:
                                            next_model = fallback_models.pop(0)
                                            body["model"] = next_model
                                            yield f"data: {json.dumps({'status': f'⏳ {model_name} busy. Swapping to fallback: {next_model}...'})}\n\n".encode()
                                            model_name = next_model
                                            await asyncio.sleep(1)
                                            continue
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
                                            if fallback_models:
                                                next_model = fallback_models.pop(0)
                                                body["model"] = next_model
                                                yield f"data: {json.dumps({'status': f'⏳ {model_name} rate limited. Swapping to fallback: {next_model}...'})}\n\n".encode()
                                                model_name = next_model
                                                await asyncio.sleep(1)
                                                continue
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
            # CRITICAL FIX: Must return a response even on exception, otherwise stream hangs
            async def _error_stream():
                yield f"data: {json.dumps({'error': {'message': f'Internal proxy error: {str(e)}'}})}\n\n".encode()
            return StreamingResponse(_error_stream(), media_type="text/event-stream")

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
        # Minimal request to validate the key — using confirmed stable free-tier model
        test_body = {
            "model": "meta-llama/llama-3.3-70b-instruct:free",
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
                elif resp.status == 429:
                    # 429 = rate limited, but the key itself IS valid
                    return {"valid": True, "message": "API key is valid ✅ (models busy, but key works)"}
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



