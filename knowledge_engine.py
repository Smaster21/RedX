import os
import chromadb
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
        # Note: This will download the model (~80MB) on first run
        self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        
        # Create or Get the 'security_knowledge' collection
        self.collection = self.client.get_or_create_collection(
            name="redx_security_brain",
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"} # Using cosine similarity for better relevance
        )

    def add_knowledge(self, content, metadata=None):
        """
        Adds a new chunk of knowledge with deduplication.
        """
        if not content or len(content.strip()) < 20:
            return False
            
        # 1. Simple Deduplication Check
        # Check if similar content already exists (score > 0.9)
        results = self.collection.query(
            query_texts=[content],
            n_results=1
        )
        
        if results['distances'] and results['distances'][0]:
            # Cosine distance: 0 is identical, 1 is opposite.
            # We want to ignore if distance < 0.1 (meaning > 90% similar)
            if results['distances'][0][0] < 0.1:
                return False
        
        # 2. Add New Entry
        doc_id = f"id_{datetime.now().timestamp()}"
        final_metadata = {
            "timestamp": datetime.now().isoformat(),
            "source": "web_search"
        }
        if metadata:
            final_metadata.update(metadata)
            
        self.collection.add(
            documents=[content],
            metadatas=[final_metadata],
            ids=[doc_id]
        )
        return True

    def search_knowledge(self, query, n_results=5):
        """
        Retrieves relevant snippets for RAG.
        """
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )
        
        # Return only results with good similarity (distance < 0.5)
        filtered_docs = []
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                if results['distances'][0][i] < 0.5:
                    filtered_docs.append(doc)
                    
        return filtered_docs

    def list_all(self):
        """
        Returns all stored knowledge for the Vault UI.
        """
        data = self.collection.get()
        # Format for UI
        items = []
        for i in range(len(data['ids'])):
            items.append({
                "id": data['ids'][i],
                "content": data['documents'][i],
                "metadata": data['metadatas'][i]
            })
        return items

    def delete_item(self, doc_id):
        """
        Deletes a specific knowledge chunk.
        """
        self.collection.delete(ids=[doc_id])
        return True

# Singleton instance
engine = KnowledgeEngine()
