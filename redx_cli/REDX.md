# RedX Project Config

## Project Context
This is a penetration testing and AI agent project built with Python + FastAPI.

## Tech Stack
- Language: Python 3.11+
- Backend: FastAPI (redx_chatbot), autonomous CLI agent (redx_cli)
- API: OpenRouter (multi-model fallback)
- Target environment: Kali Linux

## Conventions
- Use `&&` to chain shell commands in EXECUTE blocks
- Always verify file paths with `ls` before reading
- For pentest tasks, follow NIST SP 800-115 phases
- Prefer structured output (JSON) for scan results

## Off-Limits
- Never delete project files without confirmation
- Never push to git without user approval
