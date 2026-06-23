"""
RedX Knowledge Engine v6.0 (Pure Python)
- Completely replaces ChromaDB and SentenceTransformers with lightweight JSON lexical search.
- Solves all httpx, huggingface, and asyncio event-loop crashing issues natively.
"""
import os, json, re, asyncio, logging, math
import networkx as nx
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

def tokenize(text):
    return [w for w in re.split(r'[^a-zA-Z0-9_]+', text.lower()) if len(w) > 2]

class Neo4jConnectorMock:
    def __init__(self, uri="bolt://localhost:7687", user="neo4j", password="password"):
        pass
    def add_edge(self, sub, obj, relation): pass
    def get_graph(self): return {"nodes": [], "edges": []}

class KnowledgeEngine:
    def __init__(self, db_path=".redx_knowledge"):
        self.db_path = db_path
        if not os.path.exists(db_path):
            os.makedirs(db_path)

        self.json_path = os.path.join(db_path, "vault.json")
        self.documents = []
        self._load_json()

        self.graph = nx.MultiDiGraph()
        self.graph_path = os.path.join(db_path, "redx_graph.gml")
        self._load_graph()

    def _load_json(self):
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    self.documents = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load JSON vault: {e}")
                self.documents = []

    def _save_json(self):
        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(self.documents, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save JSON vault: {e}")

    def _load_graph(self):
        if os.path.exists(self.graph_path):
            try:
                self.graph = nx.read_gml(self.graph_path)
            except:
                self.graph = nx.MultiDiGraph()

    def _save_graph(self):
        try:
            nx.write_gml(self.graph, self.graph_path)
        except: pass

    def add_knowledge(self, content, metadata=None):
        if not content or len(content.strip()) < 20:
            return False

        if "--- KNOWLEDGE GRAPH (TRIPLES) ---" in content:
            triples_part = content.split("--- KNOWLEDGE GRAPH (TRIPLES) ---")[-1]
            for line in triples_part.split("\n"):
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) == 3:
                        sub, rel, obj = parts
                        self.graph.add_edge(sub, obj, relation=rel)
            self._save_graph()

        doc_id = f"id_{datetime.now().timestamp()}"
        final_meta = {
            "timestamp": datetime.now().isoformat(),
            "source": "verified_source"
        }
        if metadata:
            final_meta.update(metadata)
            
        self.documents.append({
            "id": doc_id,
            "content": content,
            "metadata": final_meta
        })
        self._save_json()
        logger.info(f"[Brain] Ingested document into pure-JSON memory.")
        return True

    def search_knowledge(self, query, n_results=5):
        if not self.documents:
            return []
            
        query_words = set(tokenize(query))
        if not query_words:
            return []
            
        scores = []
        for doc in self.documents:
            doc_words = set(tokenize(doc["content"]))
            overlap = len(query_words.intersection(doc_words))
            if overlap > 0:
                scores.append((overlap, doc["content"]))
                
        scores.sort(key=lambda x: x[0], reverse=True)
        filtered_docs = [doc for score, doc in scores[:n_results]]

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
        if len(self.graph.nodes) < 2: return []
        try:
            pagerank = nx.pagerank(self.graph.to_undirected())
            return [n for n, _ in sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:10]]
        except:
            return []

    def list_all(self):
        return self.documents

    def delete_item(self, doc_id):
        self.documents = [d for d in self.documents if d["id"] != doc_id]
        self._save_json()
        return True

    def clear_all(self):
        self.documents = []
        self._save_json()
        self.graph = nx.MultiDiGraph()
        if os.path.exists(self.graph_path):
            os.remove(self.graph_path)
        return True

class EngineProxy:
    def __init__(self):
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            self._engine = KnowledgeEngine()
        return self._engine

    async def _get_engine_async(self):
        if self._engine is None:
            self._engine = await asyncio.to_thread(KnowledgeEngine)
        return self._engine

    async def search_knowledge_async(self, query, n_results=5):
        engine = await self._get_engine_async()
        return await asyncio.to_thread(engine.search_knowledge, query, n_results)

    async def add_knowledge_async(self, content, metadata=None):
        engine = await self._get_engine_async()
        return await asyncio.to_thread(engine.add_knowledge, content, metadata)

    async def list_all_async(self):
        engine = await self._get_engine_async()
        return await asyncio.to_thread(engine.list_all)

    async def delete_item_async(self, doc_id):
        engine = await self._get_engine_async()
        return await asyncio.to_thread(engine.delete_item, doc_id)

    async def get_graph_data_async(self):
        engine = await self._get_engine_async()
        def _get_data():
            return {"nodes": list(engine.graph.nodes()), "edges": list(engine.graph.edges(data=True))}
        return await asyncio.to_thread(_get_data)

    def search_knowledge(self, query, n_results=5):
        return self._get_engine().search_knowledge(query, n_results)

    def add_knowledge(self, content, metadata=None):
        return self._get_engine().add_knowledge(content, metadata)

    def list_all(self):
        return self._get_engine().list_all()

    def delete_item(self, doc_id):
        return self._get_engine().delete_item(doc_id)

    def clear_all(self):
        return self._get_engine().clear_all()

    @property
    def graph(self):
        return self._get_engine().graph

# Singleton
engine = EngineProxy()
