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

@app.route("/vault/<doc_id>", methods=["DELETE"])
def delete_vault_item(doc_id):
    engine.delete_item(doc_id)
    return jsonify({"status": "deleted"})

# Create a completely permissive SSL context for Fortinet bypass
SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
SSL_CTX.set_ciphers("ALL:@SECLEVEL=0")

async def fetch_url_content(url):
    """Fetches real webpage content, bypassing search engines. Includes Deep Scrape for GitHub."""
    github_match = re.search(r"https?://github\.com/([^/]+)/([^/?#]+)(?:/tree/([^/]+)/(.*))?", url)
    if github_match:
        owner = github_match.group(1)
        repo = github_match.group(2).replace(".git", "")
        branch = github_match.group(3) or "main"
        subpath = github_match.group(4) or ""
        
        tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        output = f"--- ORIGINAL VERIFIED DATA ---\nGITHUB REPO: {owner}/{repo}\n"
        
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SSL_CTX)) as session:
                async with session.get(tree_url) as resp:
                    if resp.status == 200:
                        tree_data = await resp.json()
                        all_items = tree_data.get('tree', [])
                        
                        # Filter to target subdirectory
                        if subpath:
                            relevant = [i for i in all_items if i['path'].startswith(subpath)]
                        else:
                            relevant = all_items
                        
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
                        
                        # Build compact output: one block per skill
                        output += f"\n=== SKILL DIRECTORY MAP ({len(skills)} skills) ===\n"
                        for skill_name in sorted(skills.keys()):
                            files = skills[skill_name]
                            output += f"\n### SKILL: {skill_name}\n"
                            for f in files:
                                output += f"{f}\n"
                        
                        # === Download SKILL.md files for descriptions ===
                        skill_mds = [i for i in relevant if i['type'] == 'blob' and 
                                     i['path'].split('/')[-1].upper() in ['SKILL.MD', 'INDEX.MD']]
                        skill_mds = skill_mds[:40]
                        
                        output += f"\n\n=== SKILL DESCRIPTIONS ({len(skill_mds)} files) ===\n"
                        
                        async def download_desc(item):
                            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{item['path']}"
                            async with session.get(raw_url) as raw_resp:
                                if raw_resp.status == 200:
                                    text = await raw_resp.text()
                                    return f"\n--- {item['path']} ---\n{text[:1500]}\n"
                                return ""
                        
                        results = await asyncio.gather(*[download_desc(f) for f in skill_mds])
                        output += "".join(results)
                    else:
                        output += f"\nFailed to fetch repository tree. Status: {resp.status}"
        except Exception as e:
            output += f"\nError fetching GitHub data: {e}"
        return output

    # Generic webpage handling
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=SSL_CTX)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    soup = BeautifulSoup(await resp.text(), "html.parser")
                    text = soup.get_text(separator='\n', strip=True)
                    return f"--- ORIGINAL VERIFIED DATA ---\n{text[:15000]}"
                else:
                    return f"Failed to fetch {url}. Status code: {resp.status}"
    except Exception as e:
        return f"Error fetching {url}: {e}"

async def refine_user_prompt(api_key, user_input):
    """Uses Llama-3.1-8B-Free to professionalize the user prompt into a structured security mission."""
    refiner_model = "meta-llama/llama-3.1-8b-instruct:free"
    system_prompt = """You are the RedX Security Secretary. 
Your task is to rewrite the user's messy or short prompt into a professional, structured security mission statement.
RULES:
1. Preserve the user's core intent (URLs, specific targets, or questions).
2. Use technical, formal language.
3. Keep it to one or two sentences.
4. Output ONLY the refined prompt text. No chatter.

Example Input: "hack this site https://test.com"
Example Output: "Perform a technical security assessment of the web application at https://test.com, identifying potential attack surfaces and misconfigurations."
"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "RedX Refiner"
    }
    body = {
        "model": refiner_model,
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
    Uses the LLM to summarize raw search results into a high-density entry for the local brain.
    """
    prompt = f"""You are the RedX Scrutiny Librarian. 
Your task is to extract ORIGINAL technical data from the search results below.
CRITICAL RULES:
1. Do NOT guess. If a specific tool, CVE, or feature is not explicitly mentioned in the text, DO NOT include it.
2. If the text is messy or unrelated, return "NO_VERIFIED_DATA".
3. Prioritize file names, directories, and code snippets found in the raw results.
4. If you see a repository (like GitHub), look for the 'skills/', 'tools/', or 'scripts/' folder mentions.

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

# Pre-load the ReAct prompt
try:
    REACT_PROMPT = hub.pull("hwchase17/react")
except Exception:
    # Fallback prompt if hub is unreachable
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
                        # 0. Prompt Refinement (The Secretary)
                        if refiner_active:
                            yield f"data: {json.dumps({'status': '🪄 Refining security mission...'})}\n\n".encode()
                            refined_prompt = await refine_user_prompt(api_key, user_input)
                            yield f"data: {json.dumps({'refined_prompt': refined_prompt})}\n\n".encode()
                        else:
                            refined_prompt = user_input

                        # 1. Local Knowledge Retrieval (Secondary Brain)
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
                                search_results = await fetch_url_content(target_url)
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
                                yield f"data: {json.dumps({'status': '📥 Ingesting new knowledge...'})}\n\n".encode()
                                summary = await distill_knowledge(api_key, model_name, user_input, search_results)
                                engine.add_knowledge(summary, {"query": user_input})
                            except Exception as e:
                                print(f"[!] Knowledge ingestion failed: {str(e)}")

                        # 4. Final Context Augmentation
                        final_context = ""
                        if local_text:
                            final_context += f"\n--- LOCAL MEMORY ---\n{local_text}\n---------------------\n"
                            yield f"data: {json.dumps({'status': '✅ Local findings retrieved. Reasonong...'})}\n\n".encode()
                        
                        if search_results:
                            final_context += f"\n--- 2026 LIVE WEB SEARCH ---\n{search_results}\n----------------------------\n"
                            yield f"data: {json.dumps({'status': '✅ Factual web context retrieved. Synthesizing...'})}\n\n".encode()
                        
                        if not final_context:
                            yield f"data: {json.dumps({'status': '⚠️ No specific data found. Using core intelligence...'})}\n\n".encode()
                        
                        search_context = final_context # Reuse the variable name for downstream logic
                        
                        # LOGGING: Write context to a file for audit
                        try:
                            with open("/home/kali/Desktop/OPENROUTE/last_context.log", "w") as f:
                                f.write(f"STRICT_MODE: {strict_mode}\n")
                                f.write(f"CONTEXT:\n{search_context}\n")
                        except: pass

                        # Re-build body with search results
                        aug_msgs = []
                        has_sys = False
                        
                        if strict_mode:
                            system_injection = f"""
### SCRUTINY ENGINE ACTIVE ###
You are now in **Strict Verification Mode**. 
The following block contains ORIGINAL DATA retrieved from live 2026 sources and local memory.
1. Use the data below OR the verified context from previous messages as your ONLY source for technical specifications (CVEs, tool features, skill lists).
2. If the data below contradicts your internal training data, the data below is the ABSOLUTE TRUTH.
3. If you cannot find a specific answer in the block below OR in the established conversation history, say "I cannot verify this detail from the provided sources" instead of guessing.
4. Cite sources as [LOCAL] or [LIVE] where appropriate.
5. The COMPLETE DIRECTORY TREE section shows EVERY file and subfolder that exists. Use it to accurately list all subfolders and files.
6. The KEY FILE CONTENTS section contains the actual descriptions from SKILL.md and INDEX.md files. Use them for explanations.
7. Format your response cleanly using markdown tables or numbered lists. Do NOT repeat information.

--- ORIGINAL VERIFIED DATA ---
{search_context}
------------------------------
"""
                        else:
                            system_injection = f"""
### STANDARD REASONING ACTIVE ###
You are operating in standard mode. Below is contextual data retrieved from live sources and memory.
Feel free to use your internal training data, logical inference, and general knowledge alongside the provided context to fully answer the user's question.
IMPORTANT: The COMPLETE DIRECTORY TREE section shows EVERY file and subfolder. Use it to accurately list all subfolders and files.
The KEY FILE CONTENTS section has the actual descriptions. Use them for explanations.
Format your response cleanly using markdown tables or numbered lists. Do NOT repeat information. Present all data in ONE pass.

--- VERIFIED DATA CONTEXT ---
{search_context}
-----------------------------
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
                        fwd_headers = {
                            "Authorization": auth,
                            "Content-Type":  "application/json",
                            "HTTP-Referer":  referer,
                            "X-Title":       title,
                        }

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
    fwd_headers = {
        "Authorization": auth,
        "Content-Type":  "application/json",
        "HTTP-Referer":  referer,
        "X-Title":       title,
    }

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
            connector = aiohttp.TCPConnector(ssl=SSL_CTX, force_close=True)
            async with aiohttp.ClientSession(connector=connector) as sess:
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
    print("  RedX Proxy (LangChain Enabled)  →  http://localhost:3000")
    print("  Fortinet bypass: SSL cert check DISABLED")
    print("  Tools: DuckDuckGo Search")
    print("=" * 55)
    app.run(host="0.0.0.0", port=3000, threaded=True, debug=False)
