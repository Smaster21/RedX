# RedX CLI — Command Reference

A complete guide to all commands, with real usage examples.

---

## 🚀 Starting RedX

```bash
redx                          # Start interactive session (default)
redx --session pentest        # Named session (saved/resumable)
redx -c                       # Continue last session
redx -y "scan localhost"      # Auto-approve all commands (CAREFUL)
redx @main.py "explain this"  # Inject file context + prompt
python3 redx.py               # Same as `redx`
```

---

## 💬 Session Commands

| Command | Description |
|---|---|
| `/new` | Start a fresh session |
| `/new pentest-lab` | New named session |
| `/sessions` | List all saved sessions |
| `/resume pentest-lab` | Switch to an existing session |
| `/clear` | Wipe conversation history |
| `/undo` | Remove last exchange |
| `/compact` | AI-summarize history to save context |
| `/usage` | Token count, cost, context % |

---

## 🤖 Model Commands

```
/model                  → List all models (current shown with →)
/model gemma            → Switch to any model matching "gemma"
/model openai/gpt-oss-120b:free  → Switch by exact ID
```

**Current model priority (free, sorted by weekly usage):**
1. `nvidia/nemotron-3-super-120b-a12b:free` (669B tokens/week)
2. `poolside/laguna-m.1:free`
3. `openai/gpt-oss-120b:free`
4. `google/gemma-4-31b-it:free`
5. *(falls back automatically on error)*

---

## 📎 File Injection (@file)

```bash
# Inside the RedX prompt:
You: @report.txt what are the key findings?
You: @/etc/passwd analyze this file
You: @scan_results.json summarize vulnerabilities
You: @src/app.py @config.yml review for security issues

# From command line:
redx @target_list.txt "run recon on each"
```

---

## 🛡 TACT Skills — Core Usage

TACT = Transilience AI Community Tools (36 security skills)

```bash
/skill                        → Show all 36 skills (table)
/skill list                   → Same

/skill <name>                 → Load skill, AI asks for target
/skill <name> <target>        → Load skill + immediately start execution

/tact <target>                → Full NIST pentest (coordination skill)
```

### Quick Examples

```bash
# Reconnaissance
/skill recon 10.10.10.1
/skill reconnaissance 192.168.1.0/24
/skill osint target.com

# Web Application Testing
/skill injection app.local
/skill client-side https://target.com
/skill server-side https://api.target.com
/skill authentication https://login.target.com
/skill api-security https://api.target.com
/skill web-app-logic https://target.com

# Infrastructure
/skill infrastructure 10.0.0.1
/skill cloud-containers aws-target.com
/skill firewall-review 10.10.10.1
/skill system 10.10.10.5

# Specialized
/skill ai-threat-testing https://llm-app.com
/skill mobile-security app.apk
/skill source-code-scanning ./src
/skill blockchain-security contract.sol
/skill reverse-engineering binary.exe

# Intelligence & Reporting
/skill cve-poc-generator CVE-2024-1234
/skill cve-risk-score CVE-2024-1234
/skill ti-ingest                        # Threat intel ingestion
/skill risk-prioritiser                 # Prioritize findings
/skill dfir                             # Digital forensics & IR

# Platform Workflows
/skill hackthebox 10.10.10.X            # HackTheBox machine
/skill hackerone target.com             # Bug bounty scope

# Full Coordinated Pentest
/tact 10.10.10.1
/tact https://target.com
/tact 192.168.1.0/24
```

---

## 📋 All 36 TACT Skills — Reference Table

| Skill Name | Usage | What It Does |
|---|---|---|
| `ai-threat-testing` | `/skill ai-threat-testing <url>` | OWASP LLM Top 10, prompt injection, model extraction |
| `api-security` | `/skill api-security <url>` | REST/GraphQL/SOAP testing, auth bypass, BOLA |
| `attack-path-stitcher` | `/skill attack-path-stitcher` | Chain findings into attack paths |
| `authentication` | `/skill authentication <url>` | Login bypass, MFA flaws, session attacks |
| `blockchain-security` | `/skill blockchain-security <contract>` | Smart contract auditing |
| `client-side` | `/skill client-side <url>` | XSS, CSRF, clickjacking, DOM attacks |
| `cloud-containers` | `/skill cloud-containers <target>` | AWS/GCP/Azure, K8s, Docker escape |
| `coordination` | `/tact <target>` | Full NIST SP 800-115 pentest coordinator |
| `cryptography` | `/skill cryptography <target>` | Weak ciphers, key management, TLS flaws |
| `cve-poc-generator` | `/skill cve-poc-generator CVE-XXXX` | Generate PoC for known CVEs |
| `cve-risk-score` | `/skill cve-risk-score CVE-XXXX` | CVSS scoring + business risk |
| `dfir` | `/skill dfir <system>` | Digital forensics & incident response |
| `essential-tools` | `/skill essential-tools` | Install & configure pentest tooling |
| `firewall-review` | `/skill firewall-review <ip>` | Firewall rules, bypass techniques |
| `github-workflow` | `/skill github-workflow` | Manage findings in GitHub |
| `hackerone` | `/skill hackerone <scope>` | Bug bounty automation workflow |
| `hackthebox` | `/skill hackthebox <ip>` | HTB machine automation |
| `infrastructure` | `/skill infrastructure <ip>` | Network/server infrastructure testing |
| `injection` | `/skill injection <url>` | SQLi, NoSQLi, LDAP, XXE, SSTI, CMDi |
| `mobile-security` | `/skill mobile-security <apk/ipa>` | Android/iOS app security |
| `osint` | `/skill osint <domain>` | Open-source intelligence gathering |
| `patt-fetcher` | `/skill patt-fetcher <technique>` | Fetch PayloadsAllTheThings techniques |
| `reconnaissance` | `/skill recon <target>` | Subdomain, port scan, endpoint enum |
| `regression-sweep` | `/skill regression-sweep` | Re-test previously found vulns |
| `reverse-engineering` | `/skill reverse-engineering <binary>` | Binary analysis, disassembly |
| `risk-prioritiser` | `/skill risk-prioritiser` | Prioritize findings by risk/impact |
| `script-generator` | `/skill script-generator <task>` | Generate custom exploit scripts |
| `server-side` | `/skill server-side <url>` | SSRF, path traversal, RCE, deserialization |
| `skill-prune` | `/skill skill-prune` | Remove outdated skills (meta) |
| `skill-update` | `/skill skill-update` | Update skill definitions (meta) |
| `social-engineering` | `/skill social-engineering <target>` | Phishing, pretexting frameworks |
| `source-code-scanning` | `/skill source-code-scanning ./src` | SAST, secret detection, dependency audit |
| `system` | `/skill system <ip>` | Local privilege escalation, post-exploit |
| `techstack-identification` | `/skill techstack <url>` | Passive tech fingerprinting |
| `ti-ingest` | `/skill ti-ingest` | Ingest threat intelligence feeds |
| `web-app-logic` | `/skill web-app-logic <url>` | Business logic flaws, race conditions |

---

## ⚡ Execution Approval Flow

When the AI decides to run a command, you'll see:
```
╭─ ⚡ Execute? ─────────────────────╮
│ nmap -sV -sC -p- 10.10.10.1      │
╰───────────────────────────────────╯
Allow? [y/N]:
```

- **`y`** → Run it, output fed back to AI
- **`N`** (or Enter) → Denied, AI finds alternative
- **`Ctrl+C`** → Cancel AI mid-stream
- **`redx -y`** → Auto-approve all (use with care)

---

## 🔧 Git Commands

```
/git status
/git diff
/git commit      → AI writes the commit message automatically
/git push
/git log --oneline -10
```

---

## 💡 Workflow Examples

### Full Pentest on a HackTheBox Machine
```bash
redx --session htb-machine
/tact 10.10.11.X          # Full coordinator skill → phases 1-6
```

### Web App Bug Bounty
```bash
redx --session bugbounty-target
/skill recon target.com
/skill techstack target.com
/skill injection https://target.com/login
/skill client-side https://target.com
/skill api-security https://api.target.com
```

### Code Review
```bash
redx
/skill source-code-scanning ./myapp
```

### CVE Research
```bash
redx
/skill cve-poc-generator CVE-2024-23897
/skill cve-risk-score CVE-2024-23897
```

---

## 🔒 Safety Blocklist

These command patterns are **always blocked** regardless of approval:
- `rm -rf /` or `rm -rf ~`
- Format commands (`mkfs`, `dd if=... of=/dev/sd`)
- Fork bombs
- Mass deletion patterns

---

## 📁 File Locations

| Path | Purpose |
|---|---|
| `~/.redx/sessions/` | Saved sessions (JSON) |
| `~/.local/bin/redx` | CLI symlink |
| `redx_cli/libs/tact/skills/` | TACT skill files |
| `redx_cli/REDX.md` | Project config (auto-loaded) |
| `~/.redx/hooks.json` | Pre/post execute hooks |
