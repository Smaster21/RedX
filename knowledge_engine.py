import os
import chromadb
import networkx as nx
import re
from chromadb.utils import embedding_functions
from datetime import datetime

class KnowledgeEngine:
    def __init__(self, db_path=".redx_knowledge"):
        self.db_path = db_path
        # Ensure directory exists
        if not os.path.exists(db_path):
            os.makedirs(db_path)
            
        # Initialize Persistent Client
        self.client = chromadb.PersistentClient(path=db_path)
        
        # Using a lightweight local embedding model
        self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        
        # Create or Get the 'security_knowledge' collection
        self.collection = self.client.get_or_create_collection(
            name="redx_security_brain",
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"} 
        )

        # Initialize Knowledge Graph
        self.graph = nx.MultiDiGraph()
        self.graph_path = os.path.join(db_path, "redx_graph.gml")
        self._load_graph()

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
        """
        Adds a new chunk of knowledge, extracts graph triples, and deduplicates.
        Automatically chunks large text to fit embedding model limits.
        """
        if not content or len(content.strip()) < 20:
            return False
            
        # 1. Graph Triple Extraction (Look for triples block)
        if "--- KNOWLEDGE GRAPH (TRIPLES) ---" in content:
            triples_part = content.split("--- KNOWLEDGE GRAPH (TRIPLES) ---")[-1]
            for line in triples_part.split('\n'):
                if '|' in line:
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) == 3:
                        sub, rel, obj = parts
                        self.graph.add_edge(sub, obj, relation=rel)
            self._save_graph()

        # 2. Chunking Logic (Limit to ~2000 chars per chunk for better embedding quality)
        chunk_size = 2000
        overlap = 200
        chunks = []
        
        if len(content) > chunk_size:
            start = 0
            while start < len(content):
                end = start + chunk_size
                chunks.append(content[start:end])
                start += (chunk_size - overlap)
        else:
            chunks = [content]

        added_any = False
        # CPU GUARD: Limit maximum chunks per ingestion to prevent CPU pinning
        max_ingest_chunks = 25
        for i, chunk in enumerate(chunks[:max_ingest_chunks]):
            # 3. Simple Deduplication Check per chunk
            results = self.collection.query(query_texts=[chunk], n_results=1)
            if results['distances'] and results['distances'][0]:
                if results['distances'][0][0] < 0.03: 
                    continue
            
            doc_id = f"id_{datetime.now().timestamp()}_{i}"
            final_metadata = {"timestamp": datetime.now().isoformat(), "source": "verified_source"}
            if metadata: final_metadata.update(metadata)
            final_metadata["chunk"] = i
                
            self.collection.add(
                documents=[chunk],
                metadatas=[final_metadata],
                ids=[doc_id]
            )
            added_any = True
            
            # CPU BREATH: Small pause to prevent 100% CPU lock during mass embedding
            import time
            time.sleep(0.05) 
        
        # Immediate persistence flush (ChromaDB 0.4+ does this automatically on PersistentClient, 
        # but we ensure metadata is logged)
        if added_any:
            print(f"[Brain] Ingested {len(chunks)} chunks into secondary memory.")
            
        return added_any

    def search_knowledge(self, query, n_results=5):
        """
        Retrieves relevant snippets using Vector Search + Graph Neighbor Expansion.
        """
        # 1. Primary Vector Search
        results = self.collection.query(query_texts=[query], n_results=n_results)
        filtered_docs = []
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                if results['distances'][0][i] < 0.6: # Relaxed threshold for more context
                    filtered_docs.append(doc)
        
        # 2. Graph-Based Expansion (BFS 1st degree)
        # Check if the query matches any existing nodes
        expanded_context = []
        for node in self.graph.nodes():
            if node.lower() in query.lower():
                neighbors = list(self.graph.neighbors(node))
                for n in neighbors[:5]:
                    rel_data = self.graph.get_edge_data(node, n)
                    rel_str = rel_data[0]['relation'] if rel_data else "related_to"
                    expanded_context.append(f"GRAPH LINK: {node} --[{rel_str}]--> {n}")
        
        if expanded_context:
            filtered_docs.insert(0, "\n".join(expanded_context))
                    
        return filtered_docs

    def get_central_nodes(self):
        """Uses PageRank to find the most 'important' concepts in the brain."""
        if len(self.graph.nodes) < 2: return []
        try:
            pagerank = nx.pagerank(self.graph.to_undirected())
            sorted_nodes = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)
            return [n[0] for n in sorted_nodes[:10]]
        except: return []

    def list_all(self):
        data = self.collection.get()
        items = []
        for i in range(len(data['ids'])):
            items.append({
                "id": data['ids'][i],
                "content": data['documents'][i],
                "metadata": data['metadatas'][i]
            })
        return items

    def delete_item(self, doc_id):
        self.collection.delete(ids=[doc_id])
        return True

    def clear_all(self):
        """Wipes the entire vector collection and resets the graph."""
        self.client.delete_collection(name="redx_security_brain")
        self.collection = self.client.get_or_create_collection(
            name="redx_security_brain",
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"}
        )
        self.graph = nx.MultiDiGraph()
        if os.path.exists(self.graph_path):
            os.remove(self.graph_path)
        return True

# Singleton instance
engine = KnowledgeEngine()
