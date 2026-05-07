"""
RedX Proxy — bypasses Fortinet SSL inspection
Uses aiohttp with fully disabled SSL verification.
"""
import asyncio, json, ssl, traceback, os, aiohttp, random
from flask import Flask, request, Response, stream_with_context
from flask_cors import CORS

# LangChain Imports
from langchain_openai import ChatOpenAI
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_classic.agents import AgentExecutor, create_react_agent
from langchain_classic import hub

app = Flask(__name__)
CORS(app, origins="*")

OPENROUTER = "https://openrouter.ai/api/v1"

# Create a completely permissive SSL context for Fortinet bypass
SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
SSL_CTX.set_ciphers("ALL:@SECLEVEL=0")

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
                        # Send initial search status
                        yield f"data: {json.dumps({'status': f'🔍 Searching 2026 data for: {user_input[:50]}...'})}\n\n".encode()
                        
                        # 1. Quick Search (Enhanced)
                        search_results = ""
                        try:
                            search_results = search_tool.run(f"2026 update: {user_input}")
                        except Exception as e:
                            print(f"[!] Search failed: {str(e)}")

                        # 2. Augment Prompt (Now inside stream to handle search results)
                        search_context = f"\n\n--- 2026 LIVE SEARCH RESULTS ---\n{search_results}\n---------------------------------\n" if search_results else ""
                        if search_results:
                            yield f"data: {json.dumps({'status': '✅ Factual context retrieved. Synthesizing response...'})}\n\n".encode()
                        else:
                            yield f"data: {json.dumps({'status': '⚠️ No new search data found. Using internal 2024 knowledge...'})}\n\n".encode()

                        # Re-build body with search results
                        aug_msgs = []
                        has_sys = False
                        for m in messages:
                            mc = m.copy()
                            if mc.get("role") == "system" and search_context:
                                mc["content"] += "\n\n[DEEP FACT-CHECKING]: Use the above search results for any numbers. " + search_context
                                has_sys = True
                            aug_msgs.append(mc)
                        if not has_sys and search_context:
                            aug_msgs.insert(0, {"role": "system", "content": f"CRITICAL: Priority is the following 2026 data over your memory:\n{search_context}"})
                        
                        body["messages"] = aug_msgs
                        body["temperature"] = 0.1
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
