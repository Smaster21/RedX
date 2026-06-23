"""
GitHub Deep Scraper — Multi-Agent Pipeline
Imported by proxy.py
"""
import asyncio, json, re, logging, os
import aiohttp

logger = logging.getLogger(__name__)
from github_rag import build_rag_index, query_rag, cleanup_rag



GITHUB_AGENTS = [
    {"model": "moonshotai/kimi-k2.6:free",           "fallbacks": ["moonshotai/kimi-k2.6:free",   "nvidia/nemotron-3-nano-30b-a3b:free"],                   "role": "🗂️ Structure Analyst",  "focus": "Analyze the complete file tree and project layout. Identify all modules, packages, entry points, and architectural patterns. Explain how the codebase is organized."},
    {"model": "openai/gpt-oss-120b:free",                  "fallbacks": ["poolside/laguna-m.1:free",    "meta-llama/llama-3.3-70b-instruct:free"],                "role": "📖 Docs Analyst",        "focus": "Extract and summarize all documentation: README, docs folders, inline comments. Explain purpose, features, API surface, and installation steps."},
    {"model": "qwen/qwen3-coder:free",                     "fallbacks": ["google/gemma-4-26b-a4b-it:free",          "poolside/laguna-xs.2:free"],                             "role": "🔬 Code Analyst",        "focus": "Analyze all source code files. Identify key classes, functions, algorithms, data flows, design patterns, dependencies. Explain what each major component does."},
    {"model": "nvidia/nemotron-3-super-120b-a12b:free",    "fallbacks": ["google/gemma-4-31b-it:free","nousresearch/hermes-3-llama-3.1-405b:free"],    "role": "⚙️ Config Analyst",      "focus": "Analyze all configuration files, dependencies, env vars, Dockerfiles, CI/CD pipelines. Identify the full tech stack, version pinning, and deployment setup."},
    {"model": "nousresearch/hermes-3-llama-3.1-405b:free", "fallbacks": ["google/gemma-4-31b-it:free","openai/gpt-oss-120b:free"],                     "role": "🔐 Security Analyst",    "focus": "Security analysis: find hardcoded secrets, vulnerable deps, injection sinks, auth weaknesses, exposed API keys, insecure defaults, attack surface mapping."},
]
GITHUB_SYNTHESIZER = "openai/gpt-oss-120b:free"
GITHUB_SYNTHESIZER_FALLBACKS = ["moonshotai/kimi-k2.6:free", "nvidia/nemotron-3-super-120b-a12b:free"]
_RETRY_STATUSES = {429, 503, 529, 502}
OPENROUTER = "https://openrouter.ai/api/v1"


async def multi_agent_github_stream(api_key: str, messages: list, run_builder_agent_fn, get_ssl_context_fn):
    """GitHub Deep Scraper: fetches full repo, distributes slices to 5 agents, synthesizes."""
    github_url = None
    user_query = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text","") for p in content if isinstance(p, dict))
            user_query = content
            match = re.search(r"https?://github\.com/[^\s\"')]+", content)
            if match:
                github_url = match.group(0).rstrip(".,;!?)")
            break

    if not github_url:
        yield ("data: " + json.dumps({"error": {"message": "No GitHub URL found. Please include a GitHub link."}}) + "\n\n").encode()
        return

    yield ("data: " + json.dumps({"status": f"🐙 Deep scraping: {github_url}"}) + "\n\n").encode()
    repo_data = None
    async for item in build_rag_index(github_url, get_ssl_context_fn):
        if isinstance(item, dict):
            repo_data = item
        else:
            yield ("data: " + json.dumps({"status": item}) + "\n\n").encode()

    if "error" in repo_data:
        yield ("data: " + json.dumps({"error": {"message": f"GitHub RAG failed: {repo_data['error']}"}}) + "\n\n").encode()
        return

    owner, repo = repo_data["owner"], repo_data["repo"]
    index_data = repo_data.get("index_data", {})
    clone_dir = repo_data.get("clone_dir", "")
    yield ("data: " + json.dumps({"status": f"✅ {owner}/{repo} indexed — launching 5 specialist agents..."}) + "\n\n").encode()

    # Phase 1: Parallel Execution
    yield ("data: " + json.dumps({"status": f"🤖 Phase 1: {len(GITHUB_AGENTS)} independent GitHub specialists launching in parallel..."}) + "\n\n").encode()

    async def run_agent_phase1(agent):
        if agent["role"] == "🗂️ Structure Analyst":
            repo_slice = repo_data["metadata"] + "\n\n" + repo_data.get("tree", "")
        else:
            query = user_query if user_query.strip() else agent['focus']
            rag_docs = query_rag(index_data, query, n_results=15)
            core_ctx = repo_data.get("core_context", "")
            repo_slice = (
                repo_data["metadata"] +
                "\n\n=== CORE FILES (ENTRY POINTS & ARCHITECTURE) ===\n" + core_ctx +
                "\n\n=== RELEVANT CODE CHUNKS ===\n" + rag_docs
            )
            
        agent_messages = [
            {"role": "system", "content": (
                f"You are the {agent['role']} in an elite GitHub repository analysis team.\n"
                f"YOUR MANDATE: {agent['focus']}\n\n"
                f"CRITICAL: The data below is REAL PRE-FETCHED content from '{owner}/{repo}'. "
                f"Do NOT say you cannot access GitHub — it is already provided.\n\n"
                f"[REPO DATA]\n{repo_slice}\n[END REPO DATA]"
            )},
            {"role": "user", "content": f"User request: {user_query}\n\nProvide your independent specialist analysis."}
        ]
        return await run_builder_agent_fn(api_key, agent, agent_messages, max_tokens=8192)

    phase1_results = await asyncio.gather(*[run_agent_phase1(a) for a in GITHUB_AGENTS])

    agent_outputs = []
    for i, r in enumerate(phase1_results):
        agent = GITHUB_AGENTS[i]
        if isinstance(r, dict) and r.get("content"):
            agent_outputs.append(r)
            yield ("data: " + json.dumps({"status": f"✅ Phase 1: {r['role']} (Draft) → {r.get('model','?')}"}) + "\n\n").encode()
        else:
            err = r.get("error","unavailable") if isinstance(r, dict) else "failed"
            yield ("data: " + json.dumps({"status": f"⚠️ Phase 1: {agent['role']} failed — {err}"}) + "\n\n").encode()

    if not agent_outputs:
        if clone_dir: cleanup_rag(clone_dir)
        yield ("data: " + json.dumps({"error": {"message": "All GitHub agents failed Phase 1."}}) + "\n\n").encode()
        return

    # Phase 2: Peer Review & Debate
    yield ("data: " + json.dumps({"status": f"🗣️ Phase 2: Broadcasting drafts for parallel peer review & debate..."}) + "\n\n").encode()

    draft_block = "\n\n".join(
        "=" * 60 + f"\n{r['role']} (Draft)\n" + "=" * 60 + f"\n{r['content']}"
        for r in agent_outputs
    )

    async def run_agent_phase2(agent):
        if agent["role"] == "🗂️ Structure Analyst":
            repo_slice = repo_data["metadata"] + "\n\n" + repo_data.get("tree", "")
        else:
            query = user_query if user_query.strip() else agent['focus']
            rag_docs = query_rag(index_data, query, n_results=15)
            core_ctx = repo_data.get("core_context", "")
            repo_slice = (
                repo_data["metadata"] +
                "\n\n=== CORE FILES (ENTRY POINTS & ARCHITECTURE) ===\n" + core_ctx +
                "\n\n=== RELEVANT CODE CHUNKS ===\n" + rag_docs
            )
            
        agent_messages = [
            {"role": "system", "content": (
                f"You are the {agent['role']} in an elite GitHub repository analysis team.\n"
                f"YOUR MANDATE: {agent['focus']}\n\n"
                f"CRITICAL: The data below is REAL PRE-FETCHED content from '{owner}/{repo}'.\n\n"
                f"[REPO DATA]\n{repo_slice}\n[END REPO DATA]"
            )},
            {"role": "user", "content": f"PEER REVIEW PHASE.\nHere are the independent drafts generated by your team:\n\n{draft_block}\n\nReview their work, identify any contradictions or errors, and provide your FINAL corrected analysis based on your specialized mandate."}
        ]
        return await run_builder_agent_fn(api_key, agent, agent_messages, max_tokens=8192)

    phase2_results = await asyncio.gather(*[run_agent_phase2(a) for a in GITHUB_AGENTS])

    final_outputs = []
    for i, r in enumerate(phase2_results):
        agent = GITHUB_AGENTS[i]
        if isinstance(r, dict) and r.get("content"):
            final_outputs.append(r)
            yield ("data: " + json.dumps({"status": f"🎯 Phase 2: {r['role']} (Final) → {r.get('model','?')}"}) + "\n\n").encode()

    if not final_outputs:
        final_outputs = agent_outputs

    yield ("data: " + json.dumps({"status": f"🔮 Phase 3: Synthesizing {len(final_outputs)} final debated analyses into report..."}) + "\n\n").encode()

    agent_block = "\n\n".join(
        "="*60 + f"\n{r['role']} (model: {r['model']})\n" + "="*60 + f"\n{r['content']}"
        for r in final_outputs
    )
    synthesis_prompt = f"""You are the Master GitHub Intelligence AI. Synthesize ALL specialist analyses into ONE complete report.

REPO: {owner}/{repo} | URL: {github_url}
USER REQUEST: {user_query}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPECIALIST ANALYSES ({len(final_outputs)} agents after debate):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{agent_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Write a comprehensive repository intelligence report:
1. **Project Overview** — purpose, features, tech stack
2. **Architecture & Structure** — directory layout, modules, data flow
3. **Core Source Code** — key files, classes, functions, algorithms explained
4. **Configuration & Dependencies** — full tech stack, deps, env vars, deployment
5. **Security Analysis** — vulnerabilities, secrets, attack surface
6. **Setup & Usage** — install, configure, run instructions
7. **Direct Answer** — specifically address the user's request

Rules: Quote actual code from the repo. Be comprehensive. Clean markdown with syntax-highlighted code blocks."""

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Title": "RedX GitHub Synthesizer"}
    synth_body = {"model": GITHUB_SYNTHESIZER, "messages": [{"role": "user", "content": synthesis_prompt}],
                  "stream": True, "temperature": 0.35, "frequency_penalty": 0.5, "presence_penalty": 0.3, "max_tokens": 16384}

    for synth_model in [GITHUB_SYNTHESIZER] + GITHUB_SYNTHESIZER_FALLBACKS:
        synth_body["model"] = synth_model
        try:
            ssl_ctx = get_ssl_context_fn()
            connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args)) as sess:
                async with sess.post(f"{OPENROUTER}/chat/completions", json=synth_body,
                                     headers=headers, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                    if resp.status == 200:
                        yield ("data: " + json.dumps({"status": ""}) + "\n\n").encode()
                        async for chunk in resp.content.iter_chunked(1024):
                            if chunk:
                                yield chunk
                        if clone_dir: cleanup_rag(clone_dir)
                        return
                    elif resp.status in _RETRY_STATUSES:
                        continue
                    else:
                        err = await resp.text()
                        if clone_dir: cleanup_rag(clone_dir)
                        yield ("data: " + json.dumps({"error": {"message": f"Synthesizer HTTP {resp.status}: {err[:200]}"}}) + "\n\n").encode()
                        return
        except Exception as e:
            logger.error(f"[GitHub] Synthesizer {synth_model} failed: {e}")
            continue
    if db_path and clone_dir: cleanup_rag(db_path, clone_dir)
    yield ("data: " + json.dumps({"error": {"message": "GitHub: All synthesizer models failed."}}) + "\n\n").encode()
