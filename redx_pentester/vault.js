
/* ── RedX Vault — AES-256-GCM + PBKDF2 ───────────────────
   Everything stays in localStorage, nothing leaves the browser.
──────────────────────────────────────────────────────────────── */
'use strict';

const VAULT_KEY = 'redx_vault';   // encrypted blob in localStorage
const ITER = 310_000;             // PBKDF2 iterations (OWASP 2023 rec.)
const enc = new TextEncoder();
const dec = new TextDecoder();

/* Derive a 256-bit AES-GCM key from password + salt via PBKDF2 */
async function deriveKey(password, salt) {
  const base = await crypto.subtle.importKey(
    'raw', enc.encode(password), 'PBKDF2', false, ['deriveKey']
  );
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, hash: 'SHA-256', iterations: ITER },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  );
}

/* Encode ArrayBuffer → base64 string */
function toB64(buf) {
  return btoa(String.fromCharCode(...new Uint8Array(buf)));
}

/* Decode base64 string → Uint8Array */
function fromB64(str) {
  return Uint8Array.from(atob(str), c => c.charCodeAt(0));
}

/* ── Public API ─────────────────────────────────────────────── */

/** Returns true if an encrypted vault blob exists */
function vaultExists() {
  return !!localStorage.getItem(VAULT_KEY);
}

/**
 * Encrypt apiKey with password and persist to localStorage.
 * Throws on failure.
 */
async function vaultCreate(apiKey, password) {
  const salt = crypto.getRandomValues(new Uint8Array(32));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(password, salt);
  const cipher = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    enc.encode(apiKey)
  );
  const blob = { s: toB64(salt), i: toB64(iv), c: toB64(cipher) };
  localStorage.setItem(VAULT_KEY, JSON.stringify(blob));
}

/**
 * Decrypt and return the API key.
 * Throws DOMException if password is wrong.
 */
async function vaultUnlock(password) {
  const raw = localStorage.getItem(VAULT_KEY);
  if (!raw) throw new Error('No vault found');
  const { s, i, c } = JSON.parse(raw);
  const salt = fromB64(s);
  const iv = fromB64(i);
  const cipher = fromB64(c);
  const key = await deriveKey(password, salt);
  const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, cipher);
  return dec.decode(plain);
}

/** Wipe the vault from localStorage */
function vaultDestroy() {
  localStorage.removeItem(VAULT_KEY);
}
