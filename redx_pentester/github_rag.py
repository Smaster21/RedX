import os
import re
import shutil
import tempfile
import asyncio
import logging
import aiohttp
from collections import defaultdict
import math

logger = logging.getLogger(__name__)

def tokenize(text):
    return [w for w in re.split(r'[^a-zA-Z0-9_]+', text.lower()) if len(w) > 2]

def is_core_file(rel_path: str) -> bool:
    """Heuristic to identify essential entry points and architecture files of a repository."""
    path_lower = rel_path.lower()
    
    # Specific patterns for CyberStrike
    for p in ["src/agent/agent.ts", "src/mcp/index.ts", "src/index.ts", "src/acp/agent.ts", "src/mcp/auth.ts"]:
        if p in path_lower:
            return True
            
    basename = os.path.basename(rel_path).lower()
    # Dependencies and build files
    if basename in ["package.json", "requirements.txt", "cargo.toml", "go.mod", "setup.py"]:
        return True
        
    # Standard entry files in root or src directories
    if basename in ["main.py", "app.py", "index.ts", "main.ts", "app.tsx", "main.go", "main.rs", "index.js", "app.js"]:
        parts = rel_path.split(os.sep)
        if len(parts) <= 3 or "src" in parts:
            return True
            
    return False

async def build_rag_index(url: str, get_ssl_context_fn):
    """Clones a repo and chunks it into an in-memory lexical index. Yields status strings, then a dict."""
    github_match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if not github_match:
        yield {"error": "Invalid GitHub URL"}
        return
        
    owner = github_match.group(1)
    repo  = github_match.group(2).replace(".git", "")
    clone_dir = os.path.join(tempfile.gettempdir(), f"redx_clone_{owner}_{repo}")
    
    # 1. Fetch Metadata
    base  = f"https://api.github.com/repos/{owner}/{repo}"
    ssl_ctx = get_ssl_context_fn()
    connector_args = {"ssl": ssl_ctx} if ssl_ctx else {}
    headers = {"Accept": "application/vnd.github.v3+json"}
    if os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.getenv('GITHUB_TOKEN')}"
        
    metadata = ""
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(**connector_args), headers=headers) as session:
        async with session.get(base, timeout=15) as resp:
            if resp.status == 200:
                meta = await resp.json()
                metadata = (
                    f"Repo: {meta.get('full_name')}\nDescription: {meta.get('description','N/A')}\n"
                    f"Language: {meta.get('language')} | License: {(meta.get('license') or {}).get('name','N/A')}\n"
                )
            else:
                yield {"error": f"Metadata fetch failed: {resp.status}"}
                return

    yield f"📥 Cloning {owner}/{repo} locally for lexical indexing..."
    
    # 2. Clone
    if os.path.exists(clone_dir):
        shutil.rmtree(clone_dir)
        
    proc = await asyncio.create_subprocess_shell(
        f"git clone --depth 1 https://github.com/{owner}/{repo}.git {clone_dir}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        yield {"error": f"Failed to clone: {stderr.decode()}"}
        return
        
    # 3. Traverse, chunk, and index
    yield f"⚡ Building in-memory Lexical Search Index..."
    await asyncio.sleep(0.01)
    
    chunks = []
    inverted_index = defaultdict(list)
    doc_freq = defaultdict(int)
    
    valid_exts = {".py",".js",".ts",".jsx",".tsx",".go",".rs",".c",".cpp",".java",".rb",".php",".sh",".md",".txt",".json",".yml",".yaml",".toml"}
    tree_lines = []
    core_files = {}
    
    chunk_idx = 0
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "dist", "build", "coverage", "__pycache__"}]
        for f in files:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, clone_dir)
            tree_lines.append(f"  📄 {rel_path}")
            
            ext = "." + f.rsplit(".", 1)[-1].lower() if "." in f else ""
            if ext not in valid_exts and f not in {"Dockerfile", "Makefile"}:
                continue
                
            try:
                with open(full_path, "r", encoding="utf-8") as file:
                    content = file.read()
            except Exception:
                continue
                
            # If it's a core file, save its content (up to 4000 chars) for the core context
            if is_core_file(rel_path):
                core_files[rel_path] = content[:4000]
                
            chunk_size = 1500
            overlap = 300
            start = 0
            
            while start < len(content):
                end = start + chunk_size
                chunk_text = content[start:end]
                doc_text = f"FILE: {rel_path}\nCONTENT:\n{chunk_text}"
                chunks.append(doc_text)
                
                # Tokenize and index
                words = tokenize(doc_text)
                word_counts = {}
                for w in words:
                    word_counts[w] = word_counts.get(w, 0) + 1
                    
                for w, count in word_counts.items():
                    inverted_index[w].append((chunk_idx, count))
                    doc_freq[w] += 1
                
                chunk_idx += 1
                start += chunk_size - overlap
                
                # Periodically yield control back to event loop so the server stays perfectly responsive
                if chunk_idx % 1000 == 0:
                    await asyncio.sleep(0)

    metadata += f"Total Files Indexed: {len(tree_lines)}\n"
    tree_str = f"=== FILE TREE ===\n" + "\n".join(tree_lines)
    if len(tree_str) > 10000:
        tree_str = tree_str[:10000] + "\n...[truncated]"
        
    index_data = {
        "chunks": chunks,
        "inverted_index": inverted_index,
        "doc_freq": doc_freq,
        "total_docs": len(chunks)
    }
    
    # Construct core context
    core_context_parts = []
    for fpath, fcontent in core_files.items():
        core_context_parts.append(f"FILE: {fpath}\nCONTENT:\n{fcontent}")
    core_context = "\n\n".join(core_context_parts)
    if len(core_context) > 20000:
        core_context = core_context[:20000] + "\n...[Core context truncated]"
        
    yield {"metadata": metadata, "tree": tree_str, "index_data": index_data, "core_context": core_context, "owner": owner, "repo": repo, "clone_dir": clone_dir}

def query_rag(index_data: dict, query: str, n_results: int = 15) -> str:
    """Queries the in-memory lexical index using a basic TF-IDF scoring."""
    chunks = index_data["chunks"]
    inverted_index = index_data["inverted_index"]
    doc_freq = index_data["doc_freq"]
    total_docs = index_data["total_docs"]
    
    if total_docs == 0:
        return "No relevant code found."
        
    query_words = tokenize(query)
    scores = defaultdict(float)
    
    for qw in set(query_words):
        if qw not in inverted_index:
            continue
            
        idf = math.log((total_docs - doc_freq[qw] + 0.5) / (doc_freq[qw] + 0.5) + 1.0)
        
        for doc_id, tf in inverted_index[qw]:
            # Basic term frequency weighting
            tf_weighted = tf / (tf + 1.2)
            scores[doc_id] += tf_weighted * idf
            
    if not scores:
        return "No relevant code found for query."
        
    sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_docs = [chunks[doc_id] for doc_id, score in sorted_docs[:n_results]]
    
    return "\n\n".join(top_docs)

def cleanup_rag(clone_dir: str):
    """Deletes temporary cloned repo."""
    try:
        if os.path.exists(clone_dir):
            shutil.rmtree(clone_dir)
        logger.info(f"[RAG] Cleanup successful for {clone_dir}")
    except Exception as e:
        logger.error(f"[RAG] Cleanup failed: {e}")
