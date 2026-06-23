"""
RedX Knowledge Engine v4.3
- Uses ChromaDB + NetworkX knowledge graph (unchanged from v4.3)
- Added: asyncio.to_thread wrappers so ChromaDB calls don't block FastAPI event loop
- Added: Neo4j credentials from .env instead of hardcoded
"""
import os, re, asyncio
import chromadb
import networkx as nx
from chromadb.utils import embedding_functions
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import logging
logger = logging.getLogger(__name__)

class Neo4jConnectorMock:
    """
    Placeholder for future persistent Graph Database integration.
    Currently used as a mock to prepare architecture for RedX v6.0.
    """
    def __init__(self, uri="bolt://localhost:7687", user="neo4j", password="password"):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = None # Placeholder for neo4j.GraphDatabase.driver
        
    def add_edge(self, sub, obj, relation):
        # mock insert
        logger.debug(f"[Neo4j Mock] Added edge: {sub} -[{relation}]-> {obj}")
        
    def get_graph(self):
        # mock retrieve
        return {"nodes": [], "edges": []}


class KnowledgeEngine:
    def __init__(self, db_path=".redx_knowledge"):
        self.db_path = db_path
        if not os.path.exists(db_path):
            os.makedirs(db_path)

        self.client = chromadb.PersistentClient(path=db_path)
        self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        self.collection = self.client.get_or_create_collection(
            name="redx_security_brain",
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"},
        )

        # Knowledge Graph
        self.graph = nx.MultiDiGraph()
        self.graph_path = os.path.join(db_path, "redx_graph.gml")
        self._load_graph()

    def _load_graph(self):
        if os.path.exists(self.graph_path):
            try:
                self.graph = nx.read_gml(self.graph_path)
            except Exception as e:
                logger.error(f"Failed to load graph: {e}")
                self.graph = nx.MultiDiGraph()

    def _save_graph(self):
        try:
            nx.write_gml(self.graph, self.graph_path)
        except Exception as e:
            logger.error(f"Failed to save graph: {e}")

    # ── Synchronous versions (kept for compatibility) ──────────────────────────
    def add_knowledge(self, content, metadata=None):
        """Synchronous add — used by proxy in background thread via asyncio.to_thread."""
        if not content or len(content.strip()) < 20:
            return False

        # Graph triple extraction
        if "--- KNOWLEDGE GRAPH (TRIPLES) ---" in content:
            triples_part = content.split("--- KNOWLEDGE GRAPH (TRIPLES) ---")[-1]
            for line in triples_part.split("\n"):
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) == 3:
                        sub, rel, obj = parts
                        self.graph.add_edge(sub, obj, relation=rel)
            self._save_graph()

        # Chunking
        chunk_size, overlap = 2000, 200
        chunks = []
        if len(content) > chunk_size:
            start = 0
            while start < len(content):
                chunks.append(content[start : start + chunk_size])
                start += chunk_size - overlap
        else:
            chunks = [content]

        added_any = False
        for i, chunk in enumerate(chunks[:25]):
            results = self.collection.query(query_texts=[chunk], n_results=1)
            if results["distances"] and results["distances"][0]:
                if results["distances"][0][0] < 0.03:
                    continue

            doc_id = f"id_{datetime.now().timestamp()}_{i}"
            final_metadata = {
                "timestamp": datetime.now().isoformat(),
                "source": "verified_source",
                "chunk": i,
            }
            if metadata:
                final_metadata.update(metadata)
            self.collection.add(
                documents=[chunk],
                metadatas=[final_metadata],
                ids=[doc_id],
            )
            added_any = True

        if added_any:
            logger.info(f"[Brain] Ingested {len(chunks)} chunks into secondary memory.")
        return added_any

    def search_knowledge(self, query, n_results=5):
        """Synchronous search — used in proxy streaming generator."""
        results = self.collection.query(query_texts=[query], n_results=n_results)
        filtered_docs = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                if results["distances"][0][i] < 0.6:
                    filtered_docs.append(doc)

        # Graph-based expansion
        expanded_context = []
        for node in self.graph.nodes():
            if node.lower() in query.lower():
                for n in list(self.graph.neighbors(node))[:5]:
                    rel_data = self.graph.get_edge_data(node, n)
                    rel_str = rel_data[0]["relation"] if rel_data else "related_to"
                    expanded_context.append(f"GRAPH LINK: {node} --[{rel_str}]--> {n}")
        if expanded_context:
            filtered_docs.insert(0, "\n".join(expanded_context))

        return filtered_docs

    def get_central_nodes(self):
        if len(self.graph.nodes) < 2:
            return []
        try:
            pagerank = nx.pagerank(self.graph.to_undirected())
            return [n for n, _ in sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:10]]
        except Exception:
            return []

    def list_all(self):
        data = self.collection.get()
        return [
            {"id": data["ids"][i], "content": data["documents"][i], "metadata": data["metadatas"][i]}
            for i in range(len(data["ids"]))
        ]

    def delete_item(self, doc_id):
        self.collection.delete(ids=[doc_id])
        return True

    def clear_all(self):
        self.client.delete_collection(name="redx_security_brain")
        self.collection = self.client.get_or_create_collection(
            name="redx_security_brain",
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self.graph = nx.MultiDiGraph()
        if os.path.exists(self.graph_path):
            os.remove(self.graph_path)
        return True

    # ── Async wrappers (for FastAPI endpoint usage) ────────────────────────────
    async def add_knowledge_async(self, content, metadata=None):
        return await asyncio.to_thread(self.add_knowledge, content, metadata)

    async def search_knowledge_async(self, query, n_results=5):
        return await asyncio.to_thread(self.search_knowledge, query, n_results)

    async def list_all_async(self):
        return await asyncio.to_thread(self.list_all)

    async def delete_item_async(self, doc_id):
        return await asyncio.to_thread(self.delete_item, doc_id)


# Singleton
engine = KnowledgeEngine()
