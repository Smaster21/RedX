"""
RedX Proxy — bypasses Fortinet SSL inspection
Uses aiohttp with fully disabled SSL verification.
"""
import asyncio, json, ssl, traceback, os, aiohttp, random
from flask import Flask, request, Response, stream_with_context, jsonify
from flask_cors import CORS
from knowledge_engine import engine

# LangChain Imports
from langchain_openai import ChatOpenAI
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_classic.agents import AgentExecutor, create_react_agent
from langchain_classic import hub
from bs4 import BeautifulSoup
import re

app = Flask(__name__)
CORS(app, origins="*")

OPENROUTER = "https://openrouter.ai/api/v1"

# --- Vault Management Endpoints ---
@app.route("/vault", methods=["GET"])
def get_vault():
    return jsonify(engine.list_all())

@app.route("/vault", methods=["DELETE"])
def clear_all_vault():
    engine.clear_all()
    return jsonify({"status": "cleared"})

@app.route("/vault/<doc_id>", methods=["DELETE"])
def delete_vault_item(doc_id):
    engine.delete_item(doc_id)
    return jsonify({"status": "deleted"})

# Create a completely permissive SSL context for Fortinet bypass
SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
SSL_CTX.set_ciphers("ALL:@SECLEVEL=0")

async def fetch_url_content(url, query=""):
    """Fetches real webpage content, bypassing search engines. Includes Graph-Based BFS for GitHub."""
    github_match = re.search(r"https?://github\.com/([^/]+)/([^/?#]+)(?:/tree/([^/]+)/(.*))?", url)
    if github_match:
        owner = github_match.group(1)
        repo = github_match.group(2).replace(".git", "")
        branch = github_match.group(3) or "main"
        subpath = github_match.group(4) or ""
        repo_api_url = f"https://api.github.com/repos/{owner}/{repo}"
        output = f"--- ORIGINAL VERIFIED DATA ---\nGITHUB REPO: {owner}/{repo}\n"
        
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SSL_CTX)) as session:
                # 1. Detect Default Branch from API
                target_branch = branch
                try:
                    async with session.get(repo_api_url) as repo_resp:
                        if repo_resp.status == 200:
                            repo_info = await repo_resp.json()
                            # Only override if the user didn't specify a branch in the URL
                            if not github_match.group(3):
                                target_branch = repo_info.get("default_branch", "main")
                except: pass
                
                # 2. Fetch Repository Tree
                tree_url = f"{repo_api_url}/git/trees/{target_branch}?recursive=1"
                async with session.get(tree_url) as resp:
                    if resp.status == 200:
                        tree_data = await resp.json()
                        all_items = tree_data.get('tree', [])
                        
                        # [GRAPH-PHASE] Filter to target subdirectory
                        if subpath:
                            relevant = [i for i in all_items if i['path'].startswith(subpath)]
                        else:
                            relevant = all_items
                        
                        # [BFS-OPTIMIZATION] For specific subpaths, show everything
                        max_files = 500 if subpath else 150
                        is_pruned = len(relevant) > max_files
                        if is_pruned and not subpath:
                            # Only prune root-level scans
                            prefix = subpath + "/" if subpath else ""
                            allowed_depth = prefix.count('/') + 1
                            relevant = [i for i in relevant if i['path'].count('/') <= allowed_depth]
                        
                        # === Build skill-grouped compact structure ===
                        from collections import defaultdict
                        skills = defaultdict(list)
                        prefix = subpath + "/" if subpath else ""
                        
                        for item in relevant:
                            rel_path = item['path'][len(prefix):] if prefix else item['path']
                            parts = rel_path.split('/')
                            if len(parts) >= 1 and parts[0]:
                                skill_name = parts[0]
                                file_path = '/'.join(parts[1:]) if len(parts) > 1 else ""
                                item_type = "📁" if item['type'] == 'tree' else "📄"
                                if file_path:
                                    skills[skill_name].append(f"  {item_type} {file_path}")
                        
                        # Build compact output
                        output += f"\n=== SKILL DIRECTORY MAP ({'FULL' if subpath else 'SUMMARIZED'}) ===\n"
                        for skill_name in sorted(skills.keys()):
                            files = skills[skill_name]
                            output += f"\n### SKILL: {skill_name}\n"
                            for f in files[:40]: # Show up to 40 files per skill
                                output += f"{f}\n"
                            if len(files) > 40:
                                output += f"  ... and {len(files)-40} more files.\n"
                        
                        # === BFS PRIORITY FETCH: Download Key Docs ===
                        query_clean = query.lower()
                        keywords = re.findall(r'\w{3,}', query_clean)
                        
                        potential_files = [i for i in relevant if i['type'] == 'blob']
                        
                        # [DEDUPLICATION] Group similar files (e.g. bypass-1, bypass-2) and limit them
                        seen_patterns = defaultdict(int)
                        diverse_files = []
                        
                        # Score and Sort ALL potential files first
                        def score_item(item):
                            score = 0
                            path_lower = item['path'].lower()
                            fname = path_lower.split('/')[-1]
                            
                            # Base Keywords
                            for kw in keywords:
                                if kw in path_lower: score += 15
                            
                            # Core Docs Priority
                            if fname in ['skill.md', 'index.md', 'readme.md']: score += 20
                            if 'principles' in fname or 'resources' in fname: score += 15
                            
                            # Reference Root Bonus (High-level guidance over specific scenarios)
                            # depth of subpath + 2 (e.g. skills/name/reference/file.md)
                            sub_depth = subpath.count('/') if subpath else 0
                            item_depth = path_lower.count('/')
                            
                            if '/reference/' in path_lower:
                                if item_depth <= (sub_depth + 2):
                                    score += 15 # Huge bonus for core reference files
                                else:
                                    score += 8  # Standard bonus for scenarios
                            
                            if path_lower.endswith('.md'): score += 5
                            
                            # Slight penalty for very deep nesting
                            score -= (item_depth - sub_depth) * 0.5
                            return score

                        sorted_all = sorted(potential_files, key=score_item, reverse=True)
                        
                        for item in sorted_all:
                            # Create a 'pattern' by removing numbers and extensions
                            pattern = re.sub(r'\d+', 'N', item['path'].lower()).split('.')[0]
                            if seen_patterns[pattern] < 3: # Allow more diversity but still prevent spam
                                diverse_files.append(item)
                                seen_patterns[pattern] += 1
                        
                        sorted_files = diverse_files[:45] # Expanded to 45 for total coverage
                        
                        output += f"\n\n=== VERIFIED SOURCE CONTENT (Top {len(sorted_files)} nodes) ===\n"
                        
                        # [ADVANCED SCRAPER] Semaphore + Retries
                        sem = asyncio.Semaphore(5) # Max 5 parallel downloads
                        download_count = 0

                        async def download_desc(item):
                            nonlocal download_count
                            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{target_branch}/{item['path']}"
                            async with sem:
                                for attempt in range(3): # 3 Retries
                                    try:
                                        async with session.get(raw_url, timeout=15) as raw_resp:
                                            if raw_resp.status == 200:
                                                text = await raw_resp.text()
                                                download_count += 1
                                                return f"\n--- {item['path']} ---\n{text[:6000]}\n"
                                            elif raw_resp.status == 429: # Rate limited
                                                await asyncio.sleep(2 * (attempt + 1))
                                    except:
                                        await asyncio.sleep(1)
                            return f"\n--- {item['path']} ---\n[Error: Content could not be retrieved after 3 attempts]\n"
                        
                        # Note: We need to collect results from the gather
                        results = []
                        for f in sorted_files:
                            results.append(download_desc(f))
                        
                        raw_contents = await asyncio.gather(*results)
                        output += "".join(raw_contents)
                    else:
                        output += f"\nFailed to fetch repository tree. Status: {resp.status}"
        except Exception as e:
            print(f"[!] fetch_url_content Error: {str(e)}")
            output += f"\nError accessing source: {str(e)}"
        
        return output
    
    # Generic Web Fetcher (for non-GitHub URLs)
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SSL_CTX)) as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    for tag in soup(['script', 'style', 'header', 'footer', 'nav']):
                        tag.decompose()
                    text = soup.get_text(separator='\n')
                    clean_text = re.sub(r'\n+', '\n', text).strip()
                    return f"--- LIVE SOURCE: {url} ---\n{clean_text[:5000]}"
    except: pass
    return f"Failed to access URL: {url}"

async def refine_user_prompt(api_key, user_input):
    """
    Acts as 'The Secretary'. Rewrites vague user inputs into precise security missions.
    """
    system_prompt = """You are the RedX Secretary. Your job is to take a vague user request 
and turn it into a high-precision security research objective.
Example: 'check for CVEs' -> 'Identify critical RCE CVEs from 2024-2026 affecting the target stack.'
Keep it under 30 words. Output ONLY the refined prompt."""
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "RedX Secretary"
    }
    body = {
        "model": "meta-llama/llama-3.1-8b-instruct:free", # Fast model for refinement
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Professionalize this: {user_input}"}
        ],
        "temperature": 0.1
    }
    
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SSL_CTX)) as session:
            async with session.post(f"{OPENROUTER}/chat/completions", headers=headers, json=body) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['choices'][0]['message']['content'].strip()
    except: pass
    return user_input # Fallback to original if refinement fails

async def distill_knowledge(api_key, model, query, raw_results):
    """
    Uses the LLM to summarize raw search results into a high-density entry with Knowledge Graph triples.
    """
    prompt = f"""You are the RedX Scrutiny Librarian. 
Your task is to extract ORIGINAL technical data and RELATIONS from the search results below.

FORMAT YOUR RESPONSE AS FOLLOWS:
--- DATA SUMMARY ---
[Technical summary of the findings, including CVEs, tool features, and key technical data]

--- KNOWLEDGE GRAPH (TRIPLES) ---
[Subject] | [Relation] | [Object]
Example: Metasploit | includes_module | exploit/multi/http/wp_crop_rce
Example: CVE-2026-1234 | affects | WordPress 6.2
(Extract at least 3-5 triples if possible)

CRITICAL RULES:
1. Do NOT guess. Only extract relations explicitly mentioned.
2. If no clear relations exist, return NO_GRAPH_DATA.
3. Prioritize file names, IPs, and CVE numbers as Nodes.

Search Query: {query}
Raw Results: {raw_results}
"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "RedX Librarian"
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    connector = aiohttp.TCPConnector(ssl=SSL_CTX)
    try:
        async with aiohttp.ClientSession(connector=connector) as sess:
            async with sess.post(f"{OPENROUTER}/chat/completions", json=body, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"[!] Distillation failed: {str(e)}")
    return raw_results[:2000] # Fallback to raw if LLM fails

# Initialize Search Tool
search_tool = DuckDuckGoSearchRun()

from langchain_core.prompts import PromptTemplate
template = """Answer the following questions as best you can. You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

Begin!

Question: {input}
Thought:{agent_scratchpad}"""
REACT_PROMPT = PromptTemplate.from_template(template)


@app.route("/proxy/v1/<path:endpoint>", methods=["POST", "GET", "OPTIONS"])
def proxy(endpoint):
    if request.method == "OPTIONS":
        r = Response(status=200)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "*"
        r.headers["Access-Control-Allow-Methods"] = "*"
        return r

    auth    = request.headers.get("Authorization", "")
    referer = request.headers.get("HTTP-Referer", "http://localhost:8080")
    title   = request.headers.get("X-Title", "RedX Chatbot")
    strict_mode = request.headers.get("X-Strict-Mode") == "true"
    refiner_active = request.headers.get("X-Prompt-Refiner") == "true"
    print(f"[DEBUG] Request for {endpoint} | Strict: {strict_mode} | Refiner: {refiner_active}")
    body    = request.get_json(silent=True) or {}
    url     = f"{OPENROUTER}/{endpoint}"
    stream  = body.get("stream", False)

    fwd_headers = {
        "Authorization": auth,
        "Content-Type":  "application/json",
        "HTTP-Referer":  referer,
        "X-Title":       title,
    }

    # --- Optimized Search-Augmented Logic for Speed ---
    if endpoint == "chat/completions" and not body.get("no_agent", False):
        try:
            api_key = auth.replace("Bearer ", "").strip()
            model_name = body.get("model", "meta-llama/llama-3.1-8b-instruct:free")
            messages = body.get("messages", [])
            
            # Extract last user message
            user_input = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    user_input = m.get("content")
                    break
            
            if stream:
                def generate_fast_stream():
                    async def _run():
                        # Set default max_tokens if not present
                        if "max_tokens" not in body:
                            body["max_tokens"] = 8192
                        
                        # 0. Prompt Refinement (The Secretary)
                        if refiner_active:
                            yield f"data: {json.dumps({'status': '🪄 Refining security mission...'})}\n\n".encode()
                            refined_prompt = await refine_user_prompt(api_key, user_input)
                            yield f"data: {json.dumps({'refined_prompt': refined_prompt})}\n\n".encode()
                        else:
                            refined_prompt = user_input

                        # 1. Local Knowledge Retrieval (Secondary Brain)
                        # [MEMORY-PURGE] If user says "next", they are switching tasks. Purge old context.
                        if "next" in user_input.lower():
                            local_context = []
                            yield f"data: {json.dumps({'status': '🧹 Task switch detected. Purging old memory...'})}\n\n".encode()
                        else:
                            yield f"data: {json.dumps({'status': '🧠 Retrieving local memory...'})}\n\n".encode()
                            local_context = engine.search_knowledge(user_input, n_results=3)
                        
                        local_text = "\n".join(local_context) if local_context else ""
                        
                        search_results = ""
                        # 2. Smart Retrieval (URL vs Search)
                        import re
                        url_match = re.search(r"https?://[^\s]+", user_input)
                        
                        # URL Memory: If no URL in current message, scan chat history for GitHub URLs
                        if not url_match:
                            for m in reversed(messages):
                                if m.get("role") == "user":
                                    history_url = re.search(r"https?://github\.com/[^\s.,;!?]+", m.get("content", ""))
                                    if history_url:
                                        url_match = history_url
                                        print(f"[URL-MEMORY] Recalled GitHub URL from chat history: {url_match.group(0)}")
                                        break
                        
                        if url_match:
                            target_url = url_match.group(0).rstrip('.,;!?')
                            yield f"data: {json.dumps({'status': f'📡 Accessing Original Source: {target_url[:40]}...'})}\n\n".encode()
                            try:
                                search_results = await fetch_url_content(target_url, user_input)
                            except Exception as e:
                                print(f"[!] URL access failed: {str(e)}")
                        else:
                            yield f"data: {json.dumps({'status': f'🔍 Searching 2026 data for: {user_input[:50]}...'})}\n\n".encode()
                            try:
                                search_results = await asyncio.to_thread(search_tool.run, f"2026 update: {user_input}")
                            except Exception as e:
                                print(f"[!] Search failed: {str(e)}")

                        if search_results:
                            try:
                                # 3. Knowledge Distillation & Ingestion
                                # SMART INGESTION: Only store if useful info was found
                                if not search_results or "Failed to fetch" in search_results or len(search_results) < 100:
                                    yield f"data: {json.dumps({'status': '⚠️ Source empty or unreachable. Skipping storage...'})}\n\n".encode()
                                else:
                                    # For GitHub, we use a Hybrid Approach:
                                    if "GITHUB REPO:" in search_results:
                                        yield f"data: {json.dumps({'status': '✅ Verified source synced directly...'})}\n\n".encode()
                                        core_docs = search_results[:8000] 
                                        yield f"data: {json.dumps({'status': '🧠 Extracting Graph Relations...'})}\n\n".encode()
                                        graph_metadata = await distill_knowledge(api_key, model_name, "structural metadata extraction", core_docs)
                                        summary = search_results + "\n\n" + graph_metadata
                                    else:
                                        yield f"data: {json.dumps({'status': '📥 Distilling knowledge...'})}\n\n".encode()
                                        summary = await distill_knowledge(api_key, model_name, user_input, search_results)
                                    
                                    # Final Check: Does the summary actually contain info?
                                    if "Information not found" not in summary:
                                        engine.add_knowledge(summary, {"query": user_input})
                                    else:
                                        yield f"data: {json.dumps({'status': 'ℹ️ Information not found. Skipping storage...'})}\n\n".encode()
                            except Exception as e:
                                print(f"[!] Knowledge ingestion failed: {str(e)}")

                        # 4. Final Context Augmentation
                        final_context = ""
                        
                        # [GRAPH-PHASE] Add PageRank central nodes to provide 'Security Landscape'
                        central_nodes = engine.get_central_nodes()
                        if central_nodes:
                            final_context += f"\n--- CORE KNOWLEDGE NODES (Centrality) ---\n{', '.join(central_nodes)}\n"

                        if local_text:
                            final_context += f"\n--- LOCAL MEMORY & GRAPH LINKS ---\n{local_text}\n---------------------\n"
                        
                        if search_results:
                            final_context += f"\n--- 2026 LIVE WEB SEARCH ---\n{search_results}\n----------------------------\n"
                        
                        # ADAPTIVE CONTEXT COMPRESSION: For small/vision models like Nemotron 1B
                        is_small_model = any(x in model_name.lower() for x in ["1b", "3b", "vl", "vision", "tiny"])
                        if is_small_model and len(final_context) > 2500:
                            yield f"data: {json.dumps({'status': '📉 Small model detected. Compressing context...'})}\n\n".encode()
                            final_context = final_context[:2500] + "\n... [Context truncated for model stability] ..."

                        if not final_context:
                            yield f"data: {json.dumps({'status': '⚠️ No specific data found. Using core intelligence...'})}\n\n".encode()
                        else:
                            yield f"data: {json.dumps({'status': '🧠 Handing off to model...'})}\n\n".encode()
                        
                        search_context = final_context
                        
                        # LOGGING: Write context to a file for audit
                        try:
                            with open("/home/kali/Desktop/OPENROUTE/last_context.log", "w") as f:
                                f.write(f"MODEL: {model_name}\n")
                                f.write(f"CONTEXT_SIZE: {len(search_context)}\n")
                                f.write(f"CONTEXT:\n{search_context}\n")
                        except: pass

                        # Re-build body with search results
                        aug_msgs = []
                        has_sys = False
                        
                        if strict_mode:
                            system_injection = f"""
### SCRUTINY ENGINE ACTIVE ###
You are now in **Strict Verification Mode**. 

--- PRIMARY TRUTH: LIVE VERIFIED DATA ---
{search_context}
-----------------------------------------

--- CONTEXTUAL BACKGROUND: HISTORICAL MEMORY ---
{local_text if local_text else "No related historical data."}
------------------------------------------------

STRICT RULES:
1. Use the **PRIMARY TRUTH** block as your ONLY source for technical specifications of the CURRENT target.
2. The **HISTORICAL MEMORY** is provided ONLY for cross-reference. If it contradicts the Primary Truth OR refers to a different skill/tool, you MUST IGNORE IT.
3. If information is missing from the Primary Truth, say "Information not found in the current live source".
4. Cite sources as [LIVE] or [MEMORY].
5. **VISUAL ARCHITECTURE:** When explaining complex flows, exploitation loops, or architectures, you MUST generate a high-fidelity **Mermaid.js** diagram or **SVG** code.
   - Use a premium aesthetic: rounded corners, soft gradients, and professional colors.
   - Use the "Threat Level" color scheme: Blues (Standard), Oranges (Advanced), Reds (Critical/Failed).
6. Format your response cleanly (tables/lists).
"""
                        else:
                            system_injection = f"""
### STANDARD REASONING ACTIVE ###
Below is contextual data retrieved from live sources and your historical memory.

--- LIVE DATA (PRIMARY) ---
{search_context}
---------------------------

--- RESEARCH HISTORY (SECONDARY) ---
{local_text if local_text else "No history available."}
------------------------------------

INSTRUCTIONS:
1. Prioritize the **LIVE DATA** for your response.
2. Use the **RESEARCH HISTORY** to provide broader context or link related findings.
3. **TECHNICAL VISUALIZATION:** You are a Senior Systems Architect. Always provide a **Mermaid.js** or **SVG** diagram for architectural or logic-heavy explanations.
   - Match the style of high-fidelity decision-engine diagrams.
   - Use professional, color-coded layers to distinguish between different phases of an operation.
4. Format your response cleanly using markdown tables or numbered lists.
"""

                        for m in messages:
                            mc = m.copy()
                            if mc.get("role") == "system":
                                mc["content"] += "\n\n" + system_injection
                                has_sys = True
                            # Inject the refined prompt into the final payload
                            if mc.get("role") == "user" and mc.get("content") == user_input:
                                mc["content"] = refined_prompt
                            aug_msgs.append(mc)
                        if not has_sys:
                            aug_msgs.insert(0, {"role": "system", "content": system_injection})
                        
                        # Final Scrutiny Anchor (at the very end of context)
                        if strict_mode:
                            aug_msgs.append({"role": "system", "content": "FINAL WARNING: You are in STRICT MODE. If the information is not in the --- ORIGINAL VERIFIED DATA --- block OR your verified chat history, you MUST say 'Information not found'. Do NOT guess."})
                        
                        body["messages"] = aug_msgs
                        body["temperature"] = 0.0 if strict_mode else 0.1
                        if "max_tokens" not in body:
                            body["max_tokens"] = 16384
                        # fwd_headers is now defined globally for the request scope

                        # 3. Super-Retry Loop
                        for attempt in range(10):
                            connector = aiohttp.TCPConnector(ssl=SSL_CTX, force_close=True)
                            try:
                                async with aiohttp.ClientSession(connector=connector) as sess:
                                    async with sess.post(url, json=body, headers=fwd_headers, timeout=120) as resp:
                                        if resp.status == 429 or resp.status == 503:
                                            wait = (2 ** attempt) + random.uniform(0, 1)
                                            yield f"data: {json.dumps({'status': f'⏳ Model busy. Retrying in {wait:.1f}s (Attempt {attempt+1}/10)...'})}\n\n".encode()
                                            await asyncio.sleep(wait)
                                            continue
                                        
                                        if resp.status != 200:
                                            err = await resp.text()
                                            if "busy" in err.lower() or "limit" in err.lower():
                                                wait = (2 ** attempt) + random.uniform(0, 1)
                                                yield f"data: {json.dumps({'status': f'⏳ Model reported busy. Retrying in {wait:.1f}s...'})}\n\n".encode()
                                                await asyncio.sleep(wait)
                                                continue
                                            yield f"data: {json.dumps({'error': {'message': err}})}\n\n".encode()
                                            return
                                            
                                        # Clear status before showing actual response
                                        yield f"data: {json.dumps({'status': ''})}\n\n".encode()
                                        
                                        async for chunk in resp.content.iter_chunked(1024):
                                            yield chunk
                                        
                                        if search_context:
                                            yield f"data: {json.dumps({'raw_context': search_context})}\n\n".encode()
                                        return 
                            except Exception as e:
                                if attempt < 9: 
                                    yield f"data: {json.dumps({'status': f'⚠️ Connection error. Retrying...'})}\n\n".encode()
                                    await asyncio.sleep(1)
                                    continue
                                yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n".encode()
                                return

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        ait = _run().__aiter__()
                        while True:
                            try: yield loop.run_until_complete(ait.__anext__())
                            except StopAsyncIteration: break
                            except Exception as e:
                                break
                    finally: loop.close()

                return Response(
                    stream_with_context(generate_fast_stream()),
                    content_type="text/event-stream",
                    headers={"Access-Control-Allow-Origin": "*"}
                )
            else:
                async def _fetch_fast():
                    connector = aiohttp.TCPConnector(ssl=SSL_CTX, force_close=True)
                    async with aiohttp.ClientSession(connector=connector) as sess:
                        async with sess.post(url, json=body, headers=fwd_headers) as resp:
                            return await resp.read(), resp.status, resp.headers.get("Content-Type", "")
                
                raw, status, ct = asyncio.run(_fetch_fast())
                return Response(raw, status=status, content_type=ct, headers={"Access-Control-Allow-Origin": "*"})

        except Exception as e:
            print(f"Fast Search Error: {traceback.format_exc()}")
            pass

    # --- Standard Pass-through Proxy (Existing Logic Fixed) ---
    # fwd_headers is already defined above

    if stream:
        def generate():
            async def _stream():
                connector = aiohttp.TCPConnector(ssl=SSL_CTX, force_close=True)
                async with aiohttp.ClientSession(connector=connector) as sess:
                    async with sess.post(url, json=body, headers=fwd_headers) as resp:
                        if resp.status != 200:
                            txt = await resp.text()
                            yield f"data: {json.dumps({'error': {'message': txt[:300], 'status': resp.status}})}\n\n".encode()
                            return
                        async for chunk in resp.content.iter_chunked(1024):
                            yield chunk

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                ait = _stream().__aiter__()
                while True:
                    try: yield loop.run_until_complete(ait.__anext__())
                    except StopAsyncIteration: break
                    except Exception: break
            finally: loop.close()

        return Response(
            stream_with_context(generate()),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*"},
        )
    else:
        async def _fetch():
            if "max_tokens" not in body:
                body["max_tokens"] = 8192
            timeout = aiohttp.ClientTimeout(total=120)
            connector = aiohttp.TCPConnector(ssl=SSL_CTX, force_close=True)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as sess:
                async with sess.post(url, json=body, headers=fwd_headers) as resp:
                    raw = await resp.read()
                    ct  = resp.headers.get("Content-Type", "")
                    return raw, resp.status, ct

        try:
            raw, status, ct = asyncio.run(_fetch())
        except Exception as e:
            return Response(
                json.dumps({"error": {"message": str(e)}}),
                status=500, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        return Response(
            raw, status=status,
            content_type="application/json" if "json" in ct else ct,
            headers={"Access-Control-Allow-Origin": "*"},
        )


@app.route("/health")
def health():
    return Response(
        json.dumps({"status": "ok", "ssl_verify": False, "agent": "LangChain + DDG"}),
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


if __name__ == "__main__":
    print("=" * 55)
    print("  RedX Proxy (Max Limits Active)  →  http://localhost:3000")
    print("  Fortinet bypass: SSL cert check DISABLED")
    print("  Max Tokens: 8192 | Timeout: 120s")
    print("=" * 55)
    app.run(host="0.0.0.0", port=3000, threaded=True, debug=False)

