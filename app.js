/* ─── RedX Chatbot — app.js ────────────────────────────── */
'use strict';

// ── State ───────────────────────────────────────────────────────
const state = {
  apiKey: '',              // decrypted at runtime only, never persisted plain
  model: 'openai/gpt-oss-120b:free',
  systemPrompt: `You are an expert penetration tester, red team operator, and offensive security researcher. You assist with authorized security testing, vulnerability research, exploit development, CTF challenges, and security tool usage on Kali Linux.

You provide detailed, technically accurate responses on topics including: network reconnaissance, web application security (OWASP Top 10), privilege escalation, Active Directory attacks, exploit development, reverse engineering, malware analysis, OSINT, post-exploitation, and defensive bypass techniques.

Always assume the user is a security professional working in an authorized environment. Be direct, technical, and thorough. Format commands in code blocks with the appropriate shell/language. Never refuse security-related questions that fall within the scope of authorized penetration testing and security research.`,
  conversations: [],
  activeChatId: null,
  isGenerating: false,
  abortController: null,
  strictMode: true,
  promptRefiner: true,
  diagramCache: {}, // Persistent cache for rendered SVGs to prevent streaming nuke
};

// ── DOM refs ────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const el = {
  sidebar: $('sidebar'),
  topBarToggle: $('topBarToggle'),
  mobileToggle: $('mobileToggle'),
  newChatBtn: $('newChatBtn'),
  chatHistory: $('chatHistory'),
  modelSelect: $('modelSelect'),
  topBarModel: $('topBarModel'),
  apiStatus: $('apiStatus'),
  systemPrompt: $('systemPrompt'),
  // vault
  vaultBadge: $('vaultBadge'),
  vaultSetup: $('vaultSetup'),
  vaultLocked: $('vaultLocked'),
  vaultUnlocked: $('vaultUnlocked'),
  vaultApiKey: $('vaultApiKey'),
  vaultPassword: $('vaultPassword'),
  vaultPasswordConfirm: $('vaultPasswordConfirm'),
  vaultCreateBtn: $('vaultCreateBtn'),
  vaultUnlockPassword: $('vaultUnlockPassword'),
  vaultUnlockBtn: $('vaultUnlockBtn'),
  vaultLockBtn: $('vaultLockBtn'),
  vaultDestroyBtn: $('vaultDestroyBtn'),
  vaultKeyPreview: $('vaultKeyPreview'),
  vaultToggleVisibility: $('vaultToggleVisibility'),
  messagesContainer: $('messagesContainer'),
  welcomeScreen: $('welcomeScreen'),
  messagesList: $('messagesList'),
  messageInput: $('messageInput'),
  sendBtn: document.getElementById('sendBtn'),
  stopBtn: document.getElementById('stopBtn'),
  charCount: document.getElementById('charCount'),
  clearChatBtn: $('clearChatBtn'),
  renameChatBtn: $('renameChatBtn'),
  exportBtn: $('exportBtn'),
  toast: $('toast'),
  strictModeToggle: $('strictModeToggleBtn'),
  refinerToggle: $('refinerToggleBtn'),
  // secondary brain vault
  openVaultBtn: $('openVaultBtn'),
  closeVaultBtn: $('closeVaultBtn'),
  vaultScreen: $('vaultScreen'),
  vaultItemsList: $('vaultItemsList'),
  sourceScreen: $('sourceScreen'),
  sourceContent: $('sourceContent'),
  closeSourceBtn: $('closeSourceBtn'),
};

// ── Persistence ─────────────────────────────────────────────────
function save() {
  // apiKey is NEVER stored plain — handled by vault.js
  localStorage.setItem('redx_model', state.model);
  localStorage.setItem('redx_system', state.systemPrompt);
  localStorage.setItem('redx_convos', JSON.stringify(state.conversations));
  localStorage.setItem('redx_active', state.activeChatId || '');
  localStorage.setItem('redx_strict', state.strictMode);
}

function load() {
  state.model = localStorage.getItem('redx_model') || 'google/gemma-4-31b-it:free';
  state.systemPrompt = localStorage.getItem('redx_system') || state.systemPrompt;
  state.conversations = JSON.parse(localStorage.getItem('redx_convos') || '[]');
  state.activeChatId = localStorage.getItem('redx_active') || null;
  state.strictMode = localStorage.getItem('redx_strict') === 'true';

  el.modelSelect.value = state.model;
  el.systemPrompt.value = state.systemPrompt;
  if (state.strictMode) el.strictModeToggle.classList.add('active');
  else el.strictModeToggle.classList.remove('active');

  if (state.promptRefiner) el.refinerToggle.classList.add('active');
  else el.refinerToggle.classList.remove('active');
  updateTopBarModel();
}

// ── Vault UI ─────────────────────────────────────────────────────
function vaultShowState() {
  const exists = vaultExists();
  const unlocked = !!state.apiKey;
  el.vaultSetup.style.display = exists ? 'none' : '';
  el.vaultLocked.style.display = (exists && !unlocked) ? '' : 'none';
  el.vaultUnlocked.style.display = unlocked ? '' : 'none';
  el.vaultBadge.textContent = unlocked ? 'UNLOCKED' : (exists ? 'LOCKED' : 'NOT SET');
  el.vaultBadge.className = 'vault-badge' + (unlocked ? ' unlocked' : '');
  if (unlocked) {
    el.vaultKeyPreview.textContent = '••••••••••••' + state.apiKey.slice(-6);
    el.apiStatus.textContent = '✓ Vault unlocked';
    el.apiStatus.className = 'api-status saved';
  } else {
    el.apiStatus.textContent = exists ? '🔒 Vault locked — enter password' : '';
    el.apiStatus.className = 'api-status';
  }
}

async function vaultHandleCreate() {
  const apiKey = el.vaultApiKey.value.trim();
  const pw = el.vaultPassword.value;
  const pw2 = el.vaultPasswordConfirm.value;
  if (!apiKey.startsWith('sk-or-')) { showToast('Key must start with sk-or-', 'error'); return; }
  if (pw.length < 6) { showToast('Password must be ≥ 6 characters', 'error'); return; }
  if (pw !== pw2) { showToast('Passwords do not match', 'error'); return; }
  el.vaultCreateBtn.textContent = 'Encrypting…';
  el.vaultCreateBtn.disabled = true;
  try {
    await vaultCreate(apiKey, pw);
    state.apiKey = apiKey;
    el.vaultApiKey.value = el.vaultPassword.value = el.vaultPasswordConfirm.value = '';
    vaultShowState();
    showToast('Vault created & unlocked!', 'success');
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  } finally {
    el.vaultCreateBtn.textContent = 'Create Vault';
    el.vaultCreateBtn.disabled = false;
  }
}

async function vaultHandleUnlock() {
  const pw = el.vaultUnlockPassword.value;
  if (!pw) { showToast('Enter your vault password', 'error'); return; }
  el.vaultUnlockBtn.textContent = 'Decrypting…';
  el.vaultUnlockBtn.disabled = true;
  try {
    state.apiKey = await vaultUnlock(pw);
    el.vaultUnlockPassword.value = '';
    vaultShowState();
    showToast('Vault unlocked ✓', 'success');
  } catch (e) {
    showToast('Wrong password or corrupted vault', 'error');
    el.vaultUnlockPassword.value = '';
  } finally {
    el.vaultUnlockBtn.textContent = 'Unlock Vault';
    el.vaultUnlockBtn.disabled = false;
  }
}

function vaultHandleLock() {
  state.apiKey = '';
  vaultShowState();
  showToast('Vault locked');
}

function vaultHandleDestroy() {
  if (!confirm('Destroy vault? Your encrypted API key will be deleted permanently.')) return;
  vaultDestroy();
  state.apiKey = '';
  vaultShowState();
  showToast('Vault destroyed', 'error');
}

// ── Knowledge Vault (Secondary Brain) Logic ──
async function fetchVault() {
  try {
    const resp = await fetch('http://localhost:3000/vault');
    const data = await resp.json();
    renderVault(data);
  } catch (err) {
    console.error('Failed to fetch vault:', err);
  }
}

function renderVault(items) {
  if (!items || items.length === 0) {
    el.vaultItemsList.innerHTML = '<div class="empty-vault">No intelligence stored yet. Start searching to build your brain.</div>';
    return;
  }

  el.vaultItemsList.innerHTML = items.map(item => `
    <div class="vault-item-card" data-id="${item.id}">
      <div class="vault-item-title">🔍 ${escHtml(item.metadata.query || 'Knowledge Chunk')}</div>
      <div class="vault-item-content collapsed" onclick="toggleVaultExpand(this)">${escHtml(item.content)}</div>
      <div class="vault-expand-hint" onclick="toggleVaultExpand(this.previousElementSibling)">Read More...</div>
      <div class="vault-item-meta">
        <span>${new Date(item.metadata.timestamp).toLocaleString()}</span>
        <button class="delete-vault-item-btn" onclick="deleteVaultItem('${item.id}')">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>
          Delete
        </button>
      </div>
    </div>
  `).join('');
}

function toggleVaultExpand(contentEl) {
  const isCollapsed = contentEl.classList.contains('collapsed');
  contentEl.classList.toggle('collapsed');
  const hint = contentEl.nextElementSibling;
  if (hint && hint.classList.contains('vault-expand-hint')) {
    hint.textContent = isCollapsed ? 'Show Less' : 'Read More...';
  }
}

// Make globally available
window.toggleVaultExpand = toggleVaultExpand;

async function deleteVaultItem(id) {
  try {
    await fetch(`http://localhost:3000/vault/${id}`, { method: 'DELETE' });
    fetchVault(); // Refresh
    showToast('Intelligence chunk deleted', 'success');
  } catch (err) {
    console.error('Delete failed:', err);
    showToast('Failed to delete chunk', 'error');
  }
}

async function clearVault() {
  if (!confirm('⚠️ WARNING: This will PERMANENTLY delete your entire Secondary Brain memory. Proceed?')) return;
  try {
    await fetch('http://localhost:3000/vault', { method: 'DELETE' });
    fetchVault(); // Refresh
    showToast('Secondary Brain Purged 🧠🔥', 'error');
  } catch (err) {
    console.error('Purge failed:', err);
    showToast('Failed to purge brain', 'error');
  }
}

// Make globally available for onclick
window.deleteVaultItem = deleteVaultItem;
window.clearVault = clearVault;

let keyVisible = false;
function vaultToggleKey() {
  keyVisible = !keyVisible;
  el.vaultKeyPreview.textContent = keyVisible
    ? state.apiKey
    : '••••••••••••' + state.apiKey.slice(-6);
}

// ── Toast ───────────────────────────────────────────────────────
let toastTimer;
function showToast(msg, type = '') {
  clearTimeout(toastTimer);
  el.toast.textContent = msg;
  el.toast.className = `toast ${type} show`;
  toastTimer = setTimeout(() => { el.toast.className = 'toast'; }, 3000);
}

// ── Sidebar ──────────────────────────────────────────────────────
let sidebarOpen = true;
function toggleSidebar() {
  sidebarOpen = !sidebarOpen;
  el.sidebar.classList.toggle('collapsed', !sidebarOpen);
}
function toggleMobileSidebar() {
  el.sidebar.classList.toggle('mobile-open');
}

// ── Model ───────────────────────────────────────────────────────
function updateTopBarModel() {
  const opt = el.modelSelect.options[el.modelSelect.selectedIndex];
  el.topBarModel.textContent = opt ? opt.text : state.model;
}

// (API key management is now handled by the Vault — see vaultHandle* functions above)

// ── Chat History ─────────────────────────────────────────────────
function renderHistory() {
  el.chatHistory.innerHTML = '';
  const sorted = [...state.conversations].reverse();
  if (!sorted.length) {
    el.chatHistory.innerHTML = '<div style="padding:10px 14px;font-size:0.8rem;color:var(--text3);">No chats yet</div>';
    return;
  }
  sorted.forEach(c => {
    const item = document.createElement('div');
    item.className = 'history-item' + (c.id === state.activeChatId ? ' active' : '');
    item.innerHTML = `
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
      </svg>
      <span title="${escHtml(c.title)}">${escHtml(c.title)}</span>`;
    item.addEventListener('click', () => { loadChat(c.id); if (window.innerWidth <= 720) el.sidebar.classList.remove('mobile-open'); });
    el.chatHistory.appendChild(item);
  });
}

function newChat() {
  const id = 'c' + Date.now();
  const convo = { id, title: 'New Chat', messages: [] };
  state.conversations.push(convo);
  state.activeChatId = id;
  renderHistory();
  renderMessages();
  save();
}

function loadChat(id) {
  state.activeChatId = id;
  renderHistory();
  renderMessages();
}

function activeConvo() {
  return state.conversations.find(c => c.id === state.activeChatId);
}

function clearChat() {
  const c = activeConvo();
  if (!c) return;
  if (!confirm('Are you sure you want to delete this chat permanently?')) return;
  
  state.conversations = state.conversations.filter(convo => convo.id !== c.id);
  
  if (state.conversations.length > 0) {
    state.activeChatId = state.conversations[state.conversations.length - 1].id;
  } else {
    newChat();
    return; // newChat() already calls renderHistory and renderMessages
  }
  
  renderHistory();
  renderMessages();
  save();
  showToast('Chat deleted');
}

function renameChat() {
  const c = activeConvo();
  if (!c) return;
  const newName = prompt('Enter new chat name:', c.title);
  if (newName !== null && newName.trim() !== '') {
    c.title = newName.trim();
    renderHistory();
    save();
  }
}

// ── Render Messages ──────────────────────────────────────────────
function renderMessages() {
  el.messagesList.innerHTML = '';
  const c = activeConvo();

  if (!c || !c.messages.length) {
    el.welcomeScreen.style.display = '';
    el.messagesList.style.display = 'none';
    return;
  }
  el.welcomeScreen.style.display = 'none';
  el.messagesList.style.display = '';

  c.messages.forEach(m => appendMessage(m.role, m.content, false));
  scrollBottom();
  renderMermaidDiagrams();
}

function appendMessage(role, content, animate = true) {
  el.welcomeScreen.style.display = 'none';
  el.messagesList.style.display = '';

  const msg = document.createElement('div');
  msg.className = `message ${role}`;
  if (!animate) msg.style.animation = 'none';

  const initials = role === 'user' ? 'U' : '✦';
  const author = role === 'user' ? 'You' : modelName();
  const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  msg.innerHTML = `
    <div class="avatar">${initials}</div>
    <div class="message-content">
      <div class="message-meta">
        <span class="message-author">${escHtml(author)}</span>
        <span class="message-time">${time}</span>
      </div>
      <div class="message-status" style="display:none;"></div>
      <div class="message-body">${renderMarkdown(content)}</div>
      <div class="message-actions">
        <button class="msg-action-btn" data-action="copy">Copy</button>
        ${role === 'assistant' ? '<button class="msg-action-btn" data-action="regen">↺ Retry</button>' : '<button class="msg-action-btn" data-action="edit">✎ Edit</button>'}
      </div>
    </div>`;

  msg.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.action;
      if (action === 'copy') { 
        navigator.clipboard.writeText(content); 
        showToast('Copied!', 'success'); 
      } else if (action === 'regen') { 
        regenerate(); 
      } else if (action === 'edit') {
        el.messageInput.value = content;
        el.messageInput.focus();
        el.messageInput.style.height = 'auto';
        el.messageInput.style.height = el.messageInput.scrollHeight + 'px';
        
        const c = activeConvo();
        const msgIndex = c.messages.findIndex(m => m.content === content && m.role === 'user');
        if (msgIndex !== -1) {
          c.messages = c.messages.slice(0, msgIndex);
          renderMessages();
          save();
        }
      }
    });
  });

  // Copy buttons inside code blocks
  msg.querySelectorAll('.copy-code-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const code = btn.closest('pre')?.querySelector('code')?.textContent || '';
      navigator.clipboard.writeText(code);
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
    });
  });

  el.messagesList.appendChild(msg);
  return msg;
}

// ── Streaming response ──────────────────────────────────────────
async function sendMessage() {
  if (state.isGenerating) return;
  const text = el.messageInput.value.trim();
  if (!text) return;
  if (!state.apiKey) {
    showToast('Vault is locked. Unlock or set an API key.', 'error');
    el.sidebar.classList.add('mobile-open');
    return;
  }

  // Ensure active chat
  if (!state.activeChatId) newChat();
  const c = activeConvo();
  c.messages.push({ role: 'user', content: text });
  
  el.messageInput.value = '';
  el.messageInput.style.height = 'auto';
  updateCharCount();
  
  state.isGenerating = true;
  el.sendBtn.style.display = 'none';
  el.stopBtn.style.display = 'flex';
  el.sendBtn.disabled = true;

  renderMessages();
  save();

  const typingEl = addTypingIndicator();
  state.abortController = new AbortController();

  try {
    const msgs = [];
    if (state.systemPrompt.trim()) msgs.push({ role: 'system', content: state.systemPrompt.trim() });
    msgs.push(...c.messages.map(m => ({ role: m.role, content: m.content })));

    const res = await fetch('http://localhost:3000/proxy/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${state.apiKey}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': location.href,
        'X-Title': 'RedX Chatbot',
        'X-Strict-Mode': state.strictMode,
        'X-Prompt-Refiner': state.promptRefiner,
      },
      body: JSON.stringify({ model: state.model, messages: msgs, stream: true, temperature: 0.1, max_tokens: 4096 }),
      signal: state.abortController.signal,
    });

    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e?.error?.message || `HTTP ${res.status}`);
    }

    removeTypingIndicator();
    const assistantMsg = appendMessage('assistant', '', true);
    const bodyEl = assistantMsg.querySelector('.message-body');
    const statusEl = assistantMsg.querySelector('.message-status');
    let fullContent = '';

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6).trim();
          if (dataStr === '[DONE]') break;
          try {
            const json = JSON.parse(dataStr);
            if (json.status !== undefined) {
              if (json.status) { statusEl.textContent = json.status; statusEl.style.display = 'inline-block'; }
              else { statusEl.style.display = 'none'; }
              scrollBottom();
              continue;
            }
            if (json.raw_context !== undefined) { updateSourceInspector(json.raw_context); continue; }
            const delta = json.choices[0]?.delta?.content || '';
            fullContent += delta;
            bodyEl.innerHTML = renderMarkdown(fullContent);
            scrollBottom();
          } catch (e) {}
        }
      }
    }

    c.messages.push({ role: 'assistant', content: fullContent });
    save();

  } catch (err) {
    if (err.name === 'AbortError') {
      console.log('Generation stopped by user');
      showToast('Generation stopped');
    } else {
      removeTypingIndicator();
      const isFetchError = err instanceof TypeError && err.message.toLowerCase().includes('fetch');
      const msg = isFetchError
        ? 'Network error — open the app via **http://localhost:8080** instead of file://, then retry.'
        : err.message;
      appendMessage('assistant', `⚠️ **Error:** ${msg}`);
      showToast(isFetchError ? 'Network error — use localhost:8080' : err.message, 'error');
    }
  } finally {
    removeTypingIndicator();
    state.isGenerating = false;
    state.abortController = null;
    el.sendBtn.style.display = 'flex';
    el.stopBtn.style.display = 'none';
    el.sendBtn.disabled = !el.messageInput.value.trim();
    renderMessages(); // Final UI sweep
    // Async-Guard: Wait for the visual engine to finish its work
    await renderMermaidDiagrams();
  }
}

function stopGeneration() {
  if (state.abortController) {
    state.abortController.abort();
    state.abortController = null;
  }
}

async function regenerate() {
  const c = activeConvo();
  if (!c || state.isGenerating) return;
  // Remove last assistant message
  if (c.messages.length && c.messages[c.messages.length - 1].role === 'assistant') {
    c.messages.pop();
    renderMessages();
    // Re-trigger sendMessage with empty input (but the history already has the user message)
    // We need a way to trigger just the API part
    await sendMessage_fromHistory(c);
  }
}

async function sendMessage_fromHistory(c) {
  if (state.isGenerating) return;
  state.isGenerating = true;
  el.sendBtn.style.display = 'none';
  el.stopBtn.style.display = 'flex';
  el.sendBtn.disabled = true;

  const typingEl = addTypingIndicator();
  state.abortController = new AbortController();

  try {
    const msgs = [];
    if (state.systemPrompt.trim()) msgs.push({ role: 'system', content: state.systemPrompt.trim() });
    msgs.push(...c.messages.map(m => ({ role: m.role, content: m.content })));

    const res = await fetch('http://localhost:3000/proxy/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${state.apiKey}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': location.href,
        'X-Title': 'RedX Chatbot',
        'X-Strict-Mode': state.strictMode,
        'X-Prompt-Refiner': state.promptRefiner,
      },
      body: JSON.stringify({ model: state.model, messages: msgs, stream: true, temperature: 0.1, max_tokens: 4096 }),
      signal: state.abortController.signal,
    });

    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e?.error?.message || `HTTP ${res.status}`);
    }

    removeTypingIndicator();
    const assistantMsg = appendMessage('assistant', '', true);
    const bodyEl = assistantMsg.querySelector('.message-body');
    const statusEl = assistantMsg.querySelector('.message-status');
    let fullContent = '';

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6).trim();
          if (dataStr === '[DONE]') break;
          try {
            const json = JSON.parse(dataStr);
            if (json.status !== undefined) {
              if (json.status) { statusEl.textContent = json.status; statusEl.style.display = 'inline-block'; }
              else { statusEl.style.display = 'none'; }
              scrollBottom();
              continue;
            }
            const delta = json.choices[0]?.delta?.content || '';
            fullContent += delta;
            bodyEl.innerHTML = renderMarkdown(fullContent);
            scrollBottom();
          } catch (e) {}
        }
      }
    }

    c.messages.push({ role: 'assistant', content: fullContent });
    save();

  } catch (err) {
    if (err.name === 'AbortError') {
      console.log('Generation stopped by user');
    } else {
      removeTypingIndicator();
      const isFetchError = err instanceof TypeError && err.message.toLowerCase().includes('fetch');
      const msg = isFetchError
        ? 'Network error — open the app via **http://localhost:8080** instead of file://, then retry.'
        : err.message;
      appendMessage('assistant', `⚠️ **Error:** ${msg}`);
      showToast(isFetchError ? 'Network error — use localhost:8080' : err.message, 'error');
    }
  } finally {
    removeTypingIndicator();
    state.isGenerating = false;
    state.abortController = null;
    el.sendBtn.style.display = 'flex';
    el.stopBtn.style.display = 'none';
    el.sendBtn.disabled = !el.messageInput.value.trim();
    renderMessages();
  }
}

function addTypingIndicator() {
  const wrap = document.createElement('div');
  wrap.className = 'message assistant';
  wrap.id = 'typingIndicator';
  wrap.innerHTML = `
    <div class="avatar">✦</div>
    <div class="message-content">
      <div class="message-meta"><span class="message-author">${modelName()}</span></div>
      <div class="message-body">
        <div class="typing-indicator-container">
          <div class="typing-indicator">
            <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
          </div>
          <div class="typing-indicator-text">Initializing mission...</div>
        </div>
      </div>
    </div>`;
  el.messagesList.appendChild(wrap);
  scrollBottom();
  return wrap;
}

function removeTypingIndicator() {
  const t = document.getElementById('typingIndicator');
  if (t) t.remove();
}

function updateTypingIndicator(status) {
  const typing = document.querySelector('.typing-indicator-text');
  if (typing) typing.textContent = status;
}

// ── Streaming response ──────────────────────────────────────────
async function sendMessage(fromHistoryConvo = null) {
  if (state.isGenerating) return;
  
  let text = '';
  let c = null;
  
  if (fromHistoryConvo) {
    c = fromHistoryConvo;
    text = c.messages[c.messages.length - 1].content;
  } else {
    text = el.messageInput.value.trim();
    if (!text) return;
    if (!state.activeChatId) newChat();
    c = activeConvo();
    c.messages.push({ role: 'user', content: text });
    if (c.title === 'New Chat') c.title = text.slice(0, 40) + (text.length > 40 ? '…' : '');
    appendMessage('user', text);
    el.messageInput.value = '';
    el.messageInput.style.height = 'auto';
    updateCharCount();
  }

  if (!state.apiKey) {
    showToast('🔒 Unlock your vault first!', 'error');
    appendMessage('assistant', '⚠️ **Vault is locked.** Please unlock your API Key Vault in the sidebar before sending a message.');
    return;
  }

  state.isGenerating = true;
  el.sendBtn.style.display = 'none';
  el.stopBtn.style.display = 'flex';
  el.sendBtn.disabled = true;
  state.currentRefinedPrompt = null;

  renderHistory();
  save();

  const typingEl = addTypingIndicator();
  state.abortController = new AbortController();

  try {
    const msgs = [];
    if (state.systemPrompt.trim()) msgs.push({ role: 'system', content: state.systemPrompt.trim() });
    msgs.push(...c.messages.map(m => ({ role: m.role, content: m.content })));

    const res = await fetch('http://localhost:3000/proxy/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${state.apiKey}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': location.href,
        'X-Title': 'RedX Chatbot',
        'X-Strict-Mode': state.strictMode,
        'X-Prompt-Refiner': state.promptRefiner,
      },
      body: JSON.stringify({ model: state.model, messages: msgs, stream: true, temperature: 0.1, max_tokens: 4096 }),
      signal: state.abortController.signal,
    });

    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e?.error?.message || `HTTP ${res.status}`);
    }

    removeTypingIndicator();
    const assistantMsg = appendMessage('assistant', '', true);
    const bodyEl = assistantMsg.querySelector('.message-body');
    const statusEl = assistantMsg.querySelector('.message-status');
    let fullContent = '';

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const dataStr = line.slice(6).trim();
          if (dataStr === '[DONE]') break;
          try {
            const json = JSON.parse(dataStr);
            
            // 1. Handle Status Updates
            if (json.status !== undefined) {
              if (json.status) { 
                statusEl.textContent = json.status; 
                statusEl.style.display = 'inline-block'; 
              } else { 
                statusEl.style.display = 'none'; 
              }
              scrollBottom();
              continue;
            }

            // 2. Handle Prompt Refinement
            if (json.refined_prompt) {
              state.currentRefinedPrompt = json.refined_prompt;
              continue;
            }

            // 3. Handle Context Inspection
            if (json.raw_context !== undefined) { 
              updateSourceInspector(json.raw_context); 
              continue; 
            }

            // 4. Handle Content Stream
            const delta = json.choices[0]?.delta?.content || '';
            fullContent += delta;
            
            let displayContent = fullContent;
            if (state.currentRefinedPrompt) {
              displayContent = `> [!NOTE]\n> **Refined Mission:** ${state.currentRefinedPrompt}\n\n` + fullContent;
            }
            
            bodyEl.innerHTML = renderMarkdown(displayContent);
            scrollBottom();
          } catch (e) {}
        }
      }
    }

    c.messages.push({ role: 'assistant', content: fullContent });
    save();
    
    // CRITICAL: Only render diagrams once the mission stream is 100% complete
    setTimeout(() => {
      renderMermaidDiagrams();
    }, 100);

  } catch (err) {
    if (err.name === 'AbortError') {
      showToast('Generation stopped');
    } else {
      removeTypingIndicator();
      const isFetchError = err instanceof TypeError && err.message.toLowerCase().includes('fetch');
      const msg = isFetchError
        ? 'Network error — open the app via **http://localhost:8080** instead of file://, then retry.'
        : err.message;
      appendMessage('assistant', `⚠️ **Error:** ${msg}`);
      showToast(isFetchError ? 'Network error — use localhost:8080' : err.message, 'error');
    }
  } finally {
    removeTypingIndicator();
    state.isGenerating = false;
    state.abortController = null;
    el.sendBtn.style.display = 'flex';
    el.stopBtn.style.display = 'none';
    el.sendBtn.disabled = !el.messageInput.value.trim();
  }
}

function stopGeneration() {
  if (state.abortController) {
    state.abortController.abort();
    state.abortController = null;
  }
}

async function regenerate() {
  const c = activeConvo();
  if (!c || state.isGenerating) return;
  if (c.messages.length && c.messages[c.messages.length - 1].role === 'assistant') {
    c.messages.pop();
    renderMessages();
    await sendMessage(c);
  }
}

// ── Export ──────────────────────────────────────────────────────
function exportChat() {
  const c = activeConvo();
  if (!c || !c.messages.length) { showToast('Nothing to export', 'error'); return; }
  const md = `# ${c.title}\n\n` + c.messages.map(m => `**${m.role === 'user' ? 'You' : modelName()}:**\n${m.content}`).join('\n\n---\n\n');
  const blob = new Blob([md], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${c.title.replace(/[^a-z0-9]/gi, '_').toLowerCase()}.md`;
  a.click();
  showToast('Chat exported as Markdown', 'success');
}

// ── Strict Mode Listener ─────────────────────────────────────────
el.strictModeToggle.addEventListener('click', () => {
  state.strictMode = !state.strictMode;
  el.strictModeToggle.classList.toggle('active', state.strictMode);
  save();
  showToast(state.strictMode ? 'Strict Scrutiny Active 🛡️' : 'Standard Reasoning Active');
});

el.refinerToggle.addEventListener('click', () => {
  state.promptRefiner = !state.promptRefiner;
  el.refinerToggle.classList.toggle('active', state.promptRefiner);
  save();
  showToast(state.promptRefiner ? 'Prompt Refiner Enabled 🪄' : 'Prompt Refiner Disabled');
});

// ── Markdown renderer ───────────────────────────────────────────
function renderMarkdown(text) {
  if (!text) return '';
  text = text.replace(/\[LOCAL\]/g, '<span class="source-tag source-local" onclick="window.openSourceInspector()">LOCAL</span>');
  text = text.replace(/\[LIVE\]/g, '<span class="source-tag source-live" onclick="window.openSourceInspector()">LIVE</span>');

  const renderer = new marked.Renderer();
  renderer.code = function(args) {
    const code = typeof args === 'object' ? args.text : arguments[0];
    const lang = typeof args === 'object' ? args.lang : arguments[1];
    const language = lang || 'text';
    let highlighted;
    try {
      if (language === 'mermaid') {
        let cleanCode = code.replace(/```/g, '').trim();
        cleanCode = cleanCode.replace(/^mermaid\n?/, '').trim();
        const validStarters = ['graph', 'flowchart', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 'erDiagram', 'pie', 'gantt', 'requirementDiagram'];
        const firstWord = cleanCode.split(/\s+/)[0];
        if (!validStarters.includes(firstWord)) cleanCode = 'graph TD\n' + cleanCode;
        
        // STICKY INJECTION: Check cache to prevent streaming nuke
        const hash = btoa(cleanCode).slice(0, 32);
        if (state.diagramCache[hash]) {
          return state.diagramCache[hash];
        }
        
        return `<div class="mermaid" data-hash="${hash}">${cleanCode}</div>`;
      }
      highlighted = (language && hljs.getLanguage(language)) 
        ? hljs.highlight(code, { language }).value 
        : hljs.highlightAuto(code).value;
    } catch (e) { highlighted = code; }
    
    return `
      <div class="code-block-container">
        <div class="code-header">
          <span class="code-lang">${language}</span>
          <button class="copy-code-btn" onclick="copyToClipboard(this)">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
            Copy
          </button>
        </div>
        <pre><code class="hljs language-${language}">${highlighted}</code></pre>
      </div>`;
  };

  return marked.parse(text, { renderer, gfm: true, breaks: true });
}

function copyToClipboard(btn) {
  const code = btn.closest('.code-block-container').querySelector('code').innerText;
  navigator.clipboard.writeText(code).then(() => {
    const originalText = btn.innerHTML;
    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.innerHTML = originalText; btn.classList.remove('copied'); }, 2000);
  });
}

function escHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function scrollBottom() {
  requestAnimationFrame(() => { el.messagesContainer.scrollTop = el.messagesContainer.scrollHeight; });
}
function modelName() {
  const opt = el.modelSelect.options[el.modelSelect.selectedIndex];
  return opt ? opt.text : state.model;
}
function updateCharCount() {
  const len = el.messageInput.value.length;
  el.charCount.textContent = `${len} / 32000`;
}
function autoResize(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
}

el.topBarToggle.addEventListener('click', toggleSidebar);
el.mobileToggle.addEventListener('click', toggleMobileSidebar);
document.addEventListener('click', e => {
  if (window.innerWidth <= 720 && el.sidebar.classList.contains('mobile-open')) {
    if (!el.sidebar.contains(e.target) && e.target !== el.mobileToggle) el.sidebar.classList.remove('mobile-open');
  }
});
el.newChatBtn.addEventListener('click', () => { newChat(); if (window.innerWidth <= 720) el.sidebar.classList.remove('mobile-open'); });
el.vaultCreateBtn.addEventListener('click', vaultHandleCreate);
el.vaultUnlockBtn.addEventListener('click', vaultHandleUnlock);
el.vaultLockBtn.addEventListener('click', vaultHandleLock);
el.vaultDestroyBtn.addEventListener('click', vaultHandleDestroy);
el.vaultToggleVisibility.addEventListener('click', vaultToggleKey);
el.vaultUnlockPassword.addEventListener('keydown', e => { if (e.key === 'Enter') vaultHandleUnlock(); });
el.openVaultBtn.addEventListener('click', () => { el.vaultScreen.style.display = 'flex'; fetchVault(); });
el.closeVaultBtn.addEventListener('click', () => { el.vaultScreen.style.display = 'none'; });
el.modelSelect.addEventListener('change', () => { state.model = el.modelSelect.value; updateTopBarModel(); save(); });
el.systemPrompt.addEventListener('input', () => { state.systemPrompt = el.systemPrompt.value; save(); });
el.messageInput.addEventListener('input', () => { autoResize(el.messageInput); updateCharCount(); el.sendBtn.disabled = !el.messageInput.value.trim() || state.isGenerating; });
el.messageInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!el.sendBtn.disabled) sendMessage(); } });
el.sendBtn.addEventListener('click', () => sendMessage());
el.stopBtn.addEventListener('click', stopGeneration);
el.clearChatBtn.addEventListener('click', clearChat);
el.renameChatBtn.addEventListener('click', renameChat);
el.exportBtn.addEventListener('click', exportChat);
document.querySelectorAll('.suggestion-card').forEach(card => {
  card.addEventListener('click', () => {
    el.messageInput.value = card.dataset.text;
    autoResize(el.messageInput);
    updateCharCount();
    el.sendBtn.disabled = false;
    el.messageInput.focus();
  });
});

(function init() {
  load();
  vaultShowState();
  if (state.activeChatId && state.conversations.find(c => c.id === state.activeChatId)) renderMessages();
  else if (state.conversations.length) { state.activeChatId = state.conversations[state.conversations.length - 1].id; renderMessages(); }
  renderHistory();
})();

window.openSourceInspector = () => { el.sourceScreen.style.display = 'flex'; setTimeout(() => el.sourceScreen.style.opacity = '1', 10); };
el.closeSourceBtn.addEventListener('click', () => { el.sourceScreen.style.display = 'none'; });
// --- PROFESSIONAL SYNCHRONICITY: Mutation Observer for real-time materialization ---
const renderingRegistry = new Set();
const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    if (mutation.addedNodes.length) renderMermaidDiagrams();
  }
});
observer.observe(document.body, { childList: true, subtree: true });

// Fallback Heartbeat (Low Frequency)
setInterval(renderMermaidDiagrams, 5000);

async function renderMermaidDiagrams() {
  const rawElements = document.querySelectorAll('code, pre, .mermaid');
  const validStarters = ['graph', 'flowchart', 'sequenceDiagram', 'gantt', 'classDiagram', 'stateDiagram', 'pie', 'erDiagram', 'journey', 'gitGraph', 'C4Context'];
  
  for (const el of rawElements) {
    if (el.getAttribute('data-visual-processed')) continue;
    
    const code = el.textContent.trim();
    const hasKeyword = validStarters.some(s => code.toLowerCase().startsWith(s.toLowerCase()) || code.toLowerCase().includes('\n' + s.toLowerCase()));
    if (!hasKeyword || code.length < 10) continue;

    const hash = btoa(unescape(encodeURIComponent(code))).slice(0, 32);
    if (renderingRegistry.has(hash) || document.querySelector(`[data-hash="${hash}"]`)) continue;
    
    el.setAttribute('data-visual-processed', 'true');
    renderingRegistry.add(hash);

    try {
      let healedCode = code;
      healedCode = healedCode.replace(/^\s*direction\s+.+$/gm, '');
      healedCode = healedCode.replace(/subgraph\s+([^"\n\s\[]+)/g, 'subgraph "$1"');
      healedCode = healedCode.replace(/(\w+)\[([^\]]+\?)\]/g, '$1{$2}'); 
      healedCode = healedCode.replace(/%%.*$/gm, '').replace(/\s+\n/g, '\n');

      const containerId = `mermaid-${hash}`;
      const fullHtml = `
        <div class="visual-canvas-container" data-hash="${hash}" style="margin: 30px auto !important; width: 98% !important; border: 1px solid rgba(124,109,250,0.3) !important; border-radius: 12px !important; overflow: hidden !important; background: #0d0d0d !important; box-shadow: 0 10px 40px rgba(0,0,0,0.8);">
          <div class="visual-canvas-header" style="background: #111; padding: 10px 15px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #222;">
            <span class="canvas-title" style="color: #7c6dfa; font-weight: 700; font-size: 11px; letter-spacing: 1px; text-transform: uppercase;">🛡️ Architectural Asset (Triple-Threat)</span>
            <div class="canvas-actions" style="display: flex; gap: 8px;">
              <button class="canvas-action-btn" onclick="copyNativeImage('${hash}')" style="background: #333; color: #fff; border: none; padding: 5px 12px; border-radius: 4px; font-size: 10px; cursor: pointer; font-weight: 600;">📋 COPY</button>
              <button class="canvas-action-btn" onclick="saveNativeImage('${hash}')" style="background: #7c6dfa; color: #fff; border: none; padding: 5px 12px; border-radius: 4px; font-size: 10px; cursor: pointer; font-weight: 600;">💾 SAVE</button>
            </div>
          </div>
          <div class="visual-canvas-body" style="background: #0d0d0d; padding: 25px; display: flex; justify-content: center; align-items: center; min-height: 300px; max-height: 800px; overflow: auto;">
            <div id="${containerId}" class="mermaid" style="width: 100% !important; height: auto !important; text-align: center;">
              ${healedCode}
            </div>
          </div>
        </div>
      `;

      let rootContainer = el;
      let p = el.parentElement;
      while (p && p.tagName !== 'BODY') {
        if (p.classList.contains('visual-canvas-container')) { rootContainer = null; break; }
        if (p.tagName === 'PRE' || p.classList.contains('code-block-container') || p.classList.contains('mermaid') || p.className.includes('code')) {
          rootContainer = p;
        }
        if (p.classList.contains('message-body')) break;
        p = p.parentElement;
      }
      
      if (rootContainer && rootContainer.parentNode) {
        const fragment = document.createRange().createContextualFragment(fullHtml);
        rootContainer.parentNode.replaceChild(fragment, rootContainer);
        
        const targetEl = document.getElementById(containerId);
        if (window.mermaid) {
          try {
            mermaid.init(undefined, targetEl);
            setTimeout(() => {
              if (!targetEl.querySelector('svg')) {
                triggerTier3Fallback(hash, healedCode, targetEl);
              }
            }, 800);
          } catch (e) {
            triggerTier3Fallback(hash, healedCode, targetEl);
          }
        }
      }
      setTimeout(() => renderingRegistry.delete(hash), 1000);
    } catch (e) { console.error("[RedX] Pipeline Error:", e); }
  }
}

// TIER 3: Tactical Kroki-Bridge Fallback
async function triggerTier3Fallback(hash, code, targetEl) {
  try {
    console.log('[RedX] Triggering Nuclear Kroki Bridge...');
    const encoded = btoa(unescape(encodeURIComponent(code)));
    const krokiUrl = `https://kroki.io/mermaid/svg/${encoded}`;
    const response = await fetch(krokiUrl);
    if (response.ok) {
      const svg = await response.text();
      targetEl.innerHTML = svg;
      targetEl.classList.remove('mermaid');
      targetEl.style.width = '100%';
    } else {
      targetEl.innerHTML = `<div style="color: #ff5555; padding: 20px; font-size: 12px;">🛡️ Materialization Failure.</div>`;
    }
  } catch (err) {
    targetEl.innerHTML = `<div style="color: #ff5555; padding: 20px; font-size: 12px;">🛡️ Tactical Bridge Disrupted.</div>`;
  }
}

window.saveNativeImage = function(hash) {
  const svg = document.querySelector(`[data-hash="${hash}"] svg`);
  if (!svg) return;
  const svgData = new XMLSerializer().serializeToString(svg);
  const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `redx-architectural-map-${hash.slice(0,6)}.svg`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

window.copyNativeImage = async function(hash) {
  const svg = document.querySelector(`[data-hash="${hash}"] svg`);
  if (!svg) return;
  
  try {
    const svgData = new XMLSerializer().serializeToString(svg);
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    const img = new Image();
    
    const rect = svg.getBoundingClientRect();
    canvas.width = rect.width * 2;
    canvas.height = rect.height * 2;
    
    const svgBase64 = btoa(unescape(encodeURIComponent(svgData)));
    img.src = 'data:image/svg+xml;base64,' + svgBase64;
    
    img.onload = () => {
      ctx.fillStyle = '#0d0d0d';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(async (blob) => {
        const item = new ClipboardItem({ 'image/png': blob });
        await navigator.clipboard.write([item]);
        if (typeof showToast === 'function') showToast('Asset copied!', 'success');
      });
    };
  } catch (err) {
    console.error('[RedX] Native copy failed:', err);
  }
};

window.downloadCloudImage = async function(url, filename) {
  try {
    // Detect extension based on the bridge type (SVG vs IMG)
    let finalFilename = filename;
    if (url.includes('/svg/')) {
      finalFilename = filename.replace('.png', '.svg');
    }
    
    const res = await fetch(url);
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = finalFilename;
    a.click();
    
    // Cleanup to prevent memory leaks
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    
    if (typeof showToast === 'function') showToast('Asset exported successfully!', 'success');
  } catch (err) {
    console.warn('[RedX] Download fallback triggered:', err);
    window.open(url, '_blank');
  }
}

// --- UNIVERSAL EXPORT PROTOCOL (GLOBAL SCOPE) ---
window.getPNGDataURL = async function(btn) {
  const container = btn.closest('.visual-canvas-container');
  const svg = container.querySelector('svg');
  if (!svg) throw new Error("SVG not found");
  
  const styledSVG = getStyledSVG(svg);
  const serializer = new XMLSerializer();
  const svgData = serializer.serializeToString(styledSVG);
  
  // Use a more robust encoding for Kali/Linux compatibility
  const svgBase64 = btoa(unescape(encodeURIComponent(svgData)));
  const dataURL = 'data:image/svg+xml;base64,' + svgBase64;

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const img = new Image();
  
  const rect = svg.getBoundingClientRect();
  canvas.width = rect.width * 2;
  canvas.height = rect.height * 2;
  
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      img.onload = null;
      reject('Render Timeout - Use Open Full');
    }, 5000);

    img.onload = () => {
      clearTimeout(timeout);
      ctx.fillStyle = '#080808';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      try {
        const pngURL = canvas.toDataURL('image/png');
        resolve(pngURL);
      } catch (e) { reject('Canvas Tainted'); }
    };
    img.onerror = () => {
      clearTimeout(timeout);
      reject('Decoder Error');
    };
    img.src = dataURL;
  });
};

window.downloadPNG = async function(btn) {
  try {
    btn.innerText = '⌛ Processing...';
    const dataURL = await window.getPNGDataURL(btn);
    const a = document.createElement('a');
    a.href = dataURL;
    a.download = `redx_arch_${Date.now()}.png`;
    a.click();
    btn.innerText = '💾 Save PNG';
    showToast('PNG Saved!', 'success');
  } catch (e) { 
    btn.innerText = '❌ Error';
    showToast('Download Error', 'error'); 
  }
};

window.copyDiagramAsImage = async function(btn) {
  try {
    btn.innerText = '⌛ Copying...';
    const dataURL = await window.getPNGDataURL(btn);
    const blob = await (await fetch(dataURL)).blob();
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    btn.innerText = '📋 Copy Image';
    showToast('Image in Clipboard!', 'success');
  } catch (err) {
    btn.innerText = '📋 Copy Image';
    showToast('Use "Open Full" to Copy', 'error');
  }
};

window.openInNewTab = async function(btn) {
  try {
    const dataURL = await window.getPNGDataURL(btn);
    const win = window.open();
    win.document.write(`<body style="margin:0; background:#080808; display:flex; align-items:center; justify-content:center; min-height:100vh;"><img src="${dataURL}" style="max-width:100%;"></body>`);
  } catch (e) { showToast('Popup Blocked!', 'error'); }
};

function getStyledSVG(svg) {
  const clone = svg.cloneNode(true);
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  
  // Force high-contrast inline styles
  const allElements = clone.querySelectorAll('*');
  allElements.forEach(el => {
    if (['rect', 'circle', 'polygon', 'path', 'ellipse'].includes(el.tagName.toLowerCase())) {
      el.setAttribute('fill', '#11111b');
      el.setAttribute('stroke', '#7c6dfa');
      el.setAttribute('stroke-width', '2px');
    }
    if (['text', 'tspan', 'span'].includes(el.tagName.toLowerCase())) {
      el.setAttribute('fill', '#ffffff');
      el.style.fill = '#ffffff';
      el.style.fontSize = '14px';
      el.style.fontFamily = 'Inter, sans-serif';
    }
  });

  // Critical: Use the original ViewBox and dimensions
  const originalViewBox = svg.getAttribute('viewBox');
  const originalWidth = svg.getAttribute('width') || svg.getBoundingClientRect().width;
  const originalHeight = svg.getAttribute('height') || svg.getBoundingClientRect().height;
  
  // Add a 40px buffer to prevent clipping
  const padding = 40;
  clone.setAttribute('width', parseFloat(originalWidth) + padding);
  clone.setAttribute('height', parseFloat(originalHeight) + padding);
  if (originalViewBox) {
    const vb = originalViewBox.split(' ').map(parseFloat);
    clone.setAttribute('viewBox', `${vb[0]-20} ${vb[1]-20} ${vb[2]+40} ${vb[3]+40}`);
  }
  
  return clone;
}

function downloadSVG(btn) {
  const container = btn.closest('.visual-canvas-container');
  const svg = container.querySelector('svg');
  const styledSVG = getStyledSVG(svg);
  const svgData = new XMLSerializer().serializeToString(styledSVG);
  const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `redx_diagram_${Date.now()}.svg`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('Diagram exported as High-Contrast SVG', 'success');
}

// ── Deep-Zoom Inspector Logic ────────────────────────
let currentZoom = 1;
let isPanning = false;
let startX, startY, scrollLeft, scrollTop;

function openVisualInspector(element) {
  const inspector = document.getElementById('visualInspector');
  const canvas = document.getElementById('inspectorCanvas');
  const svg = element.querySelector('svg');
  
  if (!svg) return;
  
  // Clone the SVG for the inspector
  const clone = svg.cloneNode(true);
  canvas.innerHTML = '';
  canvas.appendChild(clone);
  
  inspector.style.display = 'flex';
  currentZoom = 1;
  resetInspector();
  
  // Enable Pan
  const body = document.getElementById('inspectorBody');
  body.onmousedown = (e) => {
    isPanning = true;
    startX = e.pageX - body.offsetLeft;
    startY = e.pageY - body.offsetTop;
    scrollLeft = body.scrollLeft;
    scrollTop = body.scrollTop;
  };
  
  body.onmouseleave = () => isPanning = false;
  body.onmouseup = () => isPanning = false;
  
  body.onmousemove = (e) => {
    if (!isPanning) return;
    e.preventDefault();
    const x = e.pageX - body.offsetLeft;
    const y = e.pageY - body.offsetTop;
    const walkX = (x - startX) * 2;
    const walkY = (y - startY) * 2;
    body.scrollLeft = scrollLeft - walkX;
    body.scrollTop = scrollTop - walkY;
  };
  
  // Enable Mouse Wheel Zoom
  body.onwheel = (e) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    zoomInspector(delta);
  };
}

function zoomInspector(delta) {
  currentZoom = Math.min(Math.max(0.1, currentZoom + delta), 10);
  const canvas = document.getElementById('inspectorCanvas');
  canvas.style.transform = `scale(${currentZoom})`;
}

function resetInspector() {
  currentZoom = 1;
  const canvas = document.getElementById('inspectorCanvas');
  canvas.style.transform = 'scale(1)';
  const body = document.getElementById('inspectorBody');
  body.scrollLeft = 0;
  body.scrollTop = 0;
}

function closeVisualInspector() {
  document.getElementById('visualInspector').style.display = 'none';
}

function attachInspectorToCanvases() {
  document.querySelectorAll('.visual-canvas-body').forEach(canvas => {
    canvas.onclick = () => openVisualInspector(canvas);
  });
}

// Global exposure
window.zoomInspector = zoomInspector;
window.resetInspector = resetInspector;
window.closeVisualInspector = closeVisualInspector;
window.openVisualInspector = openVisualInspector;

// Re-attach inspector whenever diagrams are rendered
const originalRenderMermaid = renderMermaidDiagrams;
renderMermaidDiagrams = async function() {
  await originalRenderMermaid();
  attachInspectorToCanvases();
};
