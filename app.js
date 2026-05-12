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
      <div class="vault-item-title">🔍 ${item.metadata.query || 'Knowledge Chunk'}</div>
      <div class="vault-item-content collapsed" onclick="toggleVaultExpand(this)">${item.content}</div>
      <div class="vault-expand-hint" onclick="toggleVaultExpand(this.previousElementSibling)">Read More...</div>
      <div class="vault-item-meta">
        <span>${new Date(item.metadata.timestamp).toLocaleString()}</span>
        <span class="delete-vault-item" onclick="deleteVaultItem('${item.id}')" style="cursor:pointer; color: #ff5f5f;">Delete</span>
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
  if (!confirm('Are you sure you want to delete this intelligence chunk?')) return;
  try {
    await fetch(`http://localhost:3000/vault/${id}`, { method: 'DELETE' });
    fetchVault(); // Refresh
  } catch (err) {
    console.error('Delete failed:', err);
  }
}

// Make globally available for onclick
window.deleteVaultItem = deleteVaultItem;

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
    renderMessages(); // Ensure final render adds the retry buttons etc
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
        <div class="typing-indicator">
          <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
        </div>
      </div>
    </div>`;
  el.messagesList.appendChild(wrap);
  scrollBottom();
  return wrap;
}

function removeTypingIndicator() {
  const t = $('typingIndicator');
  if (t) t.remove();
}

function stopGeneration() {
  if (state.abortController) {
    state.abortController.abort();
    state.abortController = null;
  }
  state.isGenerating = false;
  el.sendBtn.style.display = 'flex';
  el.stopBtn.style.display = 'none';
  el.sendBtn.disabled = !el.messageInput.value.trim();
  removeTypingIndicator();
  showToast('Generation stopped');
}

// ── Streaming response ──────────────────────────────────────────
async function sendMessage() {
  if (state.isGenerating) return;
  const text = el.messageInput.value.trim();
  if (!text) return;
  if (!state.apiKey) {
    showToast('🔒 Unlock your vault first!', 'error');
    appendMessage('assistant', '⚠️ **Vault is locked.** Please unlock your API Key Vault in the sidebar before sending a message.');
    return;
  }

  // Ensure active convo
  if (!state.activeChatId) newChat();
  const c = activeConvo();

  // Add user message
  c.messages.push({ role: 'user', content: text });
  if (c.title === 'New Chat') c.title = text.slice(0, 40) + (text.length > 40 ? '…' : '');
  appendMessage('user', text);
  el.messageInput.value = '';
  el.messageInput.style.height = 'auto';
  updateCharCount();
  state.currentRefinedPrompt = null;
  
  // Render any user-provided Mermaid code immediately
  renderMermaidDiagrams();
  
  state.isGenerating = true;
  el.sendBtn.style.display = 'none';
  el.stopBtn.style.display = 'flex';
  el.sendBtn.disabled = true;

  renderHistory();
  save();

  const typingEl = addTypingIndicator();
  state.abortController = new AbortController();

  // Build messages for API
  const msgs = [];
  if (state.systemPrompt.trim()) msgs.push({ role: 'system', content: state.systemPrompt.trim() });
  msgs.push(...c.messages.map(m => ({ role: m.role, content: m.content })));

  let fullContent = '';

  try {
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
      body: JSON.stringify({
        model: state.model,
        messages: msgs,
        stream: true,
        temperature: 0.1,
        max_tokens: 4096,
      }),
      signal: state.abortController.signal,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err?.error?.message || `HTTP ${res.status}`);
    }

    // Replace typing indicator with streaming message
    removeTypingIndicator();
    const msgEl = appendMessage('assistant', '', true);
    const bodyEl = msgEl.querySelector('.message-body');

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
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') break;
        try {
          const json = JSON.parse(data);
          if (json.status) {
            updateTypingIndicator(json.status);
          }

          if (json.refined_prompt) {
            state.currentRefinedPrompt = json.refined_prompt;
          }

          const delta = json.choices?.[0]?.delta?.content;
          if (delta) {
            fullContent += delta;
            // Prepend refined prompt as a 'Mission' block if it exists
            let displayContent = fullContent;
            if (state.currentRefinedPrompt) {
              displayContent = `> [!NOTE]\n> **Refined Mission:** ${state.currentRefinedPrompt}\n\n` + fullContent;
            }
            bodyEl.innerHTML = renderMarkdown(displayContent);
            
            // Re-attach copy listeners
            bodyEl.querySelectorAll('.copy-code-btn').forEach(btn => {
              btn.addEventListener('click', () => {
                const code = btn.closest('.code-block-container')?.querySelector('code')?.textContent || '';
                navigator.clipboard.writeText(code);
                btn.textContent = 'Copied!';
                setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
              });
            });
            scrollBottom();
          }
        } catch (_) { }
      }
    }

    c.messages.push({ role: 'assistant', content: fullContent });
    if (state.currentRefinedPrompt) {
        // Optionally store the refined prompt in message metadata
        c.messages[c.messages.length - 1].refined = state.currentRefinedPrompt;
    }
    save();

    // Trigger Mermaid rendering ONLY when done to avoid syntax errors on incomplete blocks
    renderMermaidDiagrams();

  } catch (err) {
    if (err.name !== 'AbortError') {
      removeTypingIndicator();
      const isFetchError = err instanceof TypeError && err.message.toLowerCase().includes('fetch');
      const msg = isFetchError
        ? 'Network error — open the app via **http://localhost:8080** instead of file://, then retry.'
        : err.message;
      appendMessage('assistant', `⚠️ **Error:** ${msg}`);
      showToast(isFetchError ? 'Network error — use localhost:8080' : err.message, 'error');
    }
  } finally {
    state.isGenerating = false;
    el.sendBtn.style.display = 'flex';
    el.stopBtn.style.display = 'none';
    el.sendBtn.disabled = !el.messageInput.value.trim();
  }
}

async function regenerate() {
  const c = activeConvo();
  if (!c || state.isGenerating) return;
  // Remove last assistant message
  if (c.messages.length && c.messages[c.messages.length - 1].role === 'assistant') {
    c.messages.pop();
  }
  // Re-render and trigger send (simulate re-send of last user message)
  renderMessages();
  // We'll just directly call the API with current messages
  await sendMessage_fromHistory(c);
}

async function sendMessage_fromHistory(c) {
  if (state.isGenerating) return;
  state.isGenerating = true;
  el.sendBtn.style.display = 'none';
  el.stopBtn.style.display = 'flex';
  el.sendBtn.disabled = true;

  const typingEl = addTypingIndicator();
  state.abortController = new AbortController();

  const msgs = [];
  if (state.systemPrompt.trim()) msgs.push({ role: 'system', content: state.systemPrompt.trim() });
  msgs.push(...c.messages.map(m => ({ role: m.role, content: m.content })));

  let fullContent = '';

  try {
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
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e?.error?.message || `HTTP ${res.status}`); }

    removeTypingIndicator();
    const msgEl = appendMessage('assistant', '', true);
    const bodyEl = msgEl.querySelector('.message-body');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n'); buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') break;
        try {
          const json = JSON.parse(data);
          const delta = json.choices?.[0]?.delta?.content;
          if (delta) {
            fullContent += delta;
            bodyEl.innerHTML = renderMarkdown(fullContent);
            scrollBottom();
          }
        } catch (_) { }
      }
    }
    c.messages.push({ role: 'assistant', content: fullContent });
    save();
  } catch (err) {
    if (err.name !== 'AbortError') {
      removeTypingIndicator();
      const isFetchError = err instanceof TypeError && err.message.toLowerCase().includes('fetch');
      const msg = isFetchError
        ? 'Network error — open the app via **http://localhost:8080** instead of file://, then retry.'
        : err.message;
      appendMessage('assistant', `⚠️ **Error:** ${msg}`);
      showToast(isFetchError ? 'Network error — use localhost:8080' : err.message, 'error');
    }
  } finally {
    state.isGenerating = false;
    el.sendBtn.style.display = 'flex';
    el.stopBtn.style.display = 'none';
    el.sendBtn.disabled = !el.messageInput.value.trim();
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

  // Parse Source Tags (Clickable)
  text = text.replace(/\[LOCAL\]/g, '<span class="source-tag source-local" onclick="window.openSourceInspector()">LOCAL</span>');
  text = text.replace(/\[LIVE\]/g, '<span class="source-tag source-live" onclick="window.openSourceInspector()">LIVE</span>');

  // Handle Mission Alert
  text = text.replace(/> \[!NOTE\]\n> \*\*Refined Mission:\*\* (.*)/g, (match, p1) => {
    return `<div class="refined-mission"><div class="mission-header">🪄 Refined Mission</div><div class="mission-text">${p1}</div></div>`;
  });

  const renderer = new marked.Renderer();
  
  // Custom code block renderer (Modern marked signature: { text, lang, escaped })
  renderer.code = function(args) {
    // Check if args is an object (v11+) or separate params (v7-)
    const code = typeof args === 'object' ? args.text : arguments[0];
    const lang = typeof args === 'object' ? args.lang : arguments[1];
    
    const language = lang || 'text';
    let highlighted;
    
    try {
      if (language === 'mermaid') {
        // Aggressive Clean: remove nested backticks, 'mermaid' tags, and non-printable chars
        let cleanCode = code.replace(/```/g, '').trim();
        cleanCode = cleanCode.replace(/^mermaid\n?/, '').trim();
        
        // Remove common "AI chatter" that ends up in the block
        cleanCode = cleanCode.split('\n').filter(line => !line.includes('---')).join('\n');

        // Ensure it starts with a valid graph type if missing
        const validStarters = ['graph', 'flowchart', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 'erDiagram', 'pie', 'gantt', 'requirementDiagram'];
        const firstWord = cleanCode.split(/\s+/)[0];
        if (!validStarters.includes(firstWord)) {
            cleanCode = 'graph TD\n' + cleanCode;
        }

        return `<div class="mermaid">${cleanCode}</div>`;
      }
      if (language && hljs.getLanguage(language)) {
        highlighted = hljs.highlight(code, { language }).value;
      } else {
        highlighted = hljs.highlightAuto(code).value;
      }
    } catch (err) {
      highlighted = code;
    }
    
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
      </div>
    `;
  };

  return marked.parse(text, { 
    renderer,
    gfm: true,
    breaks: true
  });
}

// ── Copy to Clipboard ───────────────────────────────────────────
function copyToClipboard(btn) {
  const code = btn.closest('.code-block-container').querySelector('code').innerText;
  navigator.clipboard.writeText(code).then(() => {
    const originalText = btn.innerHTML;
    btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Copied!';
    btn.classList.add('copied');
    setTimeout(() => {
      btn.innerHTML = originalText;
      btn.classList.remove('copied');
    }, 2000);
  });
}

// ── Helpers ─────────────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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

// ── Input auto-resize ────────────────────────────────────────────
function autoResize(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
}

// ── Event Listeners ──────────────────────────────────────────────
el.topBarToggle.addEventListener('click', toggleSidebar);
el.mobileToggle.addEventListener('click', toggleMobileSidebar);

// Close mobile sidebar on outside click
document.addEventListener('click', e => {
  if (window.innerWidth <= 720 && el.sidebar.classList.contains('mobile-open')) {
    if (!el.sidebar.contains(e.target) && e.target !== el.mobileToggle) {
      el.sidebar.classList.remove('mobile-open');
    }
  }
});

el.newChatBtn.addEventListener('click', () => {
  newChat();
  if (window.innerWidth <= 720) el.sidebar.classList.remove('mobile-open');
});

// ── Vault listeners ─────────────────────────────────────────────
el.vaultCreateBtn.addEventListener('click', vaultHandleCreate);
el.vaultUnlockBtn.addEventListener('click', vaultHandleUnlock);
el.vaultLockBtn.addEventListener('click', vaultHandleLock);
el.vaultDestroyBtn.addEventListener('click', vaultHandleDestroy);
el.vaultToggleVisibility.addEventListener('click', vaultToggleKey);
el.vaultUnlockPassword.addEventListener('keydown', e => { if (e.key === 'Enter') vaultHandleUnlock(); });

// ── Secondary Brain (Vault) listeners ─────────────────────────────
el.openVaultBtn.addEventListener('click', () => {
  el.vaultScreen.style.display = 'flex';
  fetchVault();
});

el.closeVaultBtn.addEventListener('click', () => {
  el.vaultScreen.style.display = 'none';
});

el.modelSelect.addEventListener('change', () => {
  state.model = el.modelSelect.value;
  updateTopBarModel();
  save();
});

el.systemPrompt.addEventListener('input', () => {
  state.systemPrompt = el.systemPrompt.value;
  save();
});

el.messageInput.addEventListener('input', () => {
  autoResize(el.messageInput);
  updateCharCount();
  el.sendBtn.disabled = !el.messageInput.value.trim() || state.isGenerating;
});

el.messageInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!el.sendBtn.disabled) sendMessage();
  }
});

el.sendBtn.addEventListener('click', () => sendMessage());
el.stopBtn.addEventListener('click', stopGeneration);
el.newChatBtn.addEventListener('click', () => newChat());
el.clearChatBtn.addEventListener('click', clearChat);
el.renameChatBtn.addEventListener('click', renameChat);
el.exportBtn.addEventListener('click', exportChat);

// Suggestion cards
document.querySelectorAll('.suggestion-card').forEach(card => {
  card.addEventListener('click', () => {
    el.messageInput.value = card.dataset.text;
    autoResize(el.messageInput);
    updateCharCount();
    el.sendBtn.disabled = false;
    el.messageInput.focus();
  });
});

// ── Boot ─────────────────────────────────────────────────────────
(function init() {
  load();
  vaultShowState();

  if (state.activeChatId && state.conversations.find(c => c.id === state.activeChatId)) {
    renderMessages();
  } else if (state.conversations.length) {
    state.activeChatId = state.conversations[state.conversations.length - 1].id;
    renderMessages();
  }
  renderHistory();
})();

// ── Source Inspector Logic ──────────────────────────────────────
window.openSourceInspector = () => {
  el.sourceScreen.style.display = 'flex';
  setTimeout(() => el.sourceScreen.style.opacity = '1', 10);
};

el.closeSourceBtn.addEventListener('click', () => {
  el.sourceScreen.style.display = 'none';
});

// Store raw context globally for the inspector
let currentRawContext = '';

// Update inspector content whenever we get new context
function updateSourceInspector(context) {
  if (context) {
    currentRawContext = context;
    el.sourceContent.innerText = context;
  }
}

function updateTypingIndicator(status) {
  const typing = document.querySelector('.typing-indicator-text');
  if (typing) typing.textContent = status;
}

// Robust Mermaid Renderer
async function renderMermaidDiagrams() {
  const elements = document.querySelectorAll('.mermaid:not([data-processed="true"])');
  if (elements.length === 0) return;
  
  if (!window.mermaid) {
    setTimeout(renderMermaidDiagrams, 500); // Wait for ES module to load
    return;
  }

  for (const el of elements) {
    const code = el.textContent.trim();
    if (!code) continue;
    
    // Skip if it looks like it's still being typed (no end markers or very short)
    if (code.length < 10) continue;

    const id = 'mermaid-' + Math.random().toString(36).substr(2, 9);
    try {
      // Use render to catch errors and get SVG
      const { svg } = await mermaid.render(id, code);
      el.innerHTML = svg;
      el.setAttribute('data-processed', 'true');
    } catch (e) {
      console.error("Mermaid Render Error:", e);
      // If it's a real error (not just incomplete), show the code as a fallback
      if (state.isGenerating === false) {
         el.innerHTML = `<pre style="font-size:10px; color:var(--text3)">${escHtml(code)}</pre>`;
         el.setAttribute('data-processed', 'true');
      }
    }
  }
}
