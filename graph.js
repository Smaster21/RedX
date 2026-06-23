/* ── RedX Knowledge Graph — graph.js ─────────────────────────
   Visualization engine for the SecondaryBrain relationship graph.
   Uses vis-network loaded from CDN in index.html.
──────────────────────────────────────────────────────────────── */
'use strict';

// Graph is rendered inline via app.js renderKnowledgeGraph() function
// This file provides extended utilities for graph interaction

window.graphUtils = {
  /** Export graph data as JSON */
  exportGraphJSON: async function() {
    try {
      const res = await fetch(`${window.API_BASE || 'http://localhost:3000'}/vault/graph`, {
        headers: { 'X-Proxy-Token': localStorage.getItem('redx_proxy_token') || '' }
      });
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `redx_knowledge_graph_${Date.now()}.json`;
      a.click();
      if (typeof showToast === 'function') showToast('Graph exported!', 'success');
    } catch (e) { console.error('Graph export failed:', e); }
  },

  /** Get node count */
  getStats: async function() {
    try {
      const res = await fetch(`${window.API_BASE || 'http://localhost:3000'}/vault/graph`, {
        headers: { 'X-Proxy-Token': localStorage.getItem('redx_proxy_token') || '' }
      });
      const data = await res.json();
      return { nodes: data.nodes.length, edges: data.edges.length };
    } catch (e) { return { nodes: 0, edges: 0 }; }
  }
};
