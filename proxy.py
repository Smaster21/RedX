"""
RedX Proxy — bypasses Fortinet SSL inspection
Uses aiohttp with fully disabled SSL verification.
"""
import asyncio, json, ssl, traceback, os, aiohttp
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
            
            # 1. Quick Search (Enhanced)
            print(f"[*] Searching for: {user_input}")
            search_results = ""
            try:
                # Force search to focus on latest 2026 data
                search_results = search_tool.run(f"2026 update: {user_input}")
                print(f"[*] Search found: {len(search_results)} chars")
            except Exception as e:
                print(f"[!] Search failed: {str(e)}")

            # 2. Augment Prompt
            search_context = f"\n\n--- 2026 LIVE SEARCH RESULTS ---\n{search_results}\n---------------------------------\n" if search_results else ""

            augmented_messages = []
            has_system = False
            for m in messages:
                msg_copy = m.copy()
                if msg_copy.get("role") == "system" and search_context:
                    msg_copy["content"] = msg_copy["content"] + "\n\n[DEEP FACT-CHECKING]: Use the above search results for any numbers or counts. Ignore your 2024 data if it differs." + search_context
                    has_system = True
                augmented_messages.append(msg_copy)
            
            if not has_system and search_context:
                instr = (
                    "CRITICAL GUARDRAIL: Your internal knowledge cutoff is 2024. "
                    "The following SEARCH CONTEXT contains the LATEST information from MAY 2026.\n"
                    "1. Always prioritize this context over your internal knowledge.\n"
                    "2. NUMERICAL ACCURACY: If the search results provide a count (e.g. '45 questions'), you MUST use that number.\n"
                    "3. STRICT-RAG RULE: If specific details are missing, say 'Information Not Found'.\n\n"
                    f"{search_context}"
                )
                augmented_messages.insert(0, {"role": "system", "content": instr})
            
            # Lower temperature for deterministic technical reasoning
            body["temperature"] = 0.1
            body["messages"] = augmented_messages
            fwd_headers = {
                "Authorization": auth,
                "Content-Type":  "application/json",
                "HTTP-Referer":  referer,
                "X-Title":       title,
            }

            if stream:
                def generate_fast_stream():
                    async def _run():
                        connector = aiohttp.TCPConnector(ssl=SSL_CTX, force_close=True)
                        async with aiohttp.ClientSession(connector=connector) as sess:
                            async with sess.post(url, json=body, headers=fwd_headers) as resp:
                                if resp.status != 200:
                                    err = await resp.text()
                                    print(f"[!] OpenRouter error: {err}")
                                    yield f"data: {json.dumps({'error': {'message': err}})}\n\n".encode()
                                    return
                                async for chunk in resp.content.iter_chunked(1024):
                                    yield chunk
                    
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        ait = _run().__aiter__()
                        while True:
                            try: yield loop.run_until_complete(ait.__anext__())
                            except StopAsyncIteration: break
                            except Exception as e:
                                print(f"[!] Stream error: {e}")
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
