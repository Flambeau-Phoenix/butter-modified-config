# Butter AI Proxy: Unified Naming Scheme & Curated Model Map Guide

## Executive Summary

This document describes the redesigned configuration architecture and model registry for **Butter AI Proxy Gateway**. It solves two core architectural problems:

1. **Config Disconnect & Provider Ambiguity**: Previously, provider definitions lived at the top of `config.yaml` while model routes lived 100+ lines below with arbitrary IDs, no required provider linkage, and raw JSON pass-through without model rewriting.
2. **Client-Side Name Guessing**: Downstream clients (Studio, chat interfaces, mobile, desktop) previously had to parse raw Butter handles with regex to guess friendly names and providers.

### The Unified Design

* **Every route entry requires a `provider` and an explicit prefix route `<provider>/<model>`**.
* **Butter proxy automatically rewrites the outbound request body**, replacing the prefix route with the raw upstream `model` before sending to the upstream provider API.
* **Both `GET /v1/models` and `GET /api/models` emit `{ id, owner, label, family }`**, allowing any client to immediately render clean labels and provider badges with zero string parsing.
* **New model onboarding is one line in one place**.
* A dedicated tool (`butterproxy_config_manager.py`) provides automated CLI and GUI management for the new schema.

---

## 1. The Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ Client Applications                                                    │
│ (Studio, Chat WebUI, Phone, Desktop App, CLI)                          │
│                                                                        │
│ Reads: GET /api/models or GET /v1/models                               │
│ Renders:                                                               │
│   Label:  "Qwen 3 Coder 30B (Local)"                                   │
│   Badge:  [PCCODER] (Family: qwen)                                     │
│ Sends:    {"model": "pccoder/qwen3-coder-30b", "messages": [...]}      │
└───────────────────────────────────▲────────────────────────────────────┘
                                    │
                                    │ HTTP :8080
                                    │
┌───────────────────────────────────┴────────────────────────────────────┐
│ Butter AI Proxy Gateway                                                │
│                                                                        │
│ 1. Resolves route: "pccoder/qwen3-coder-30b"                           │
│ 2. Finds target provider: "pccoder"                                    │
│ 3. Rewrites outbound JSON: "model" -> "qwen3-coder-30b"                │
│ 4. Injects provider auth & headers                                     │
│ 5. Dispatches request to provider endpoint                             │
└───────────────────────────────────▲────────────────────────────────────┘
                                    │
            ┌───────────────────────┼────────────────────────┐
            │                       │                        │
            ▼                       ▼                        ▼
    [NVIDIA NIM Cloud]      [Local Workstation]       [Local Ollama]
    https://integrate.      http://100.113.98.79:     http://127.0.0.1:
    api.nvidia.com/v1       11491/v1 (pccoder)        11434/v1
    Receives:               Receives:                 Receives:
    {"model":"kimi-k3"}     {"model":"qwen3-coder-    {"model":"llama3.2:3b"}
                            30b"}
```

---

## 2. The New Configuration Schema

In `config.yaml`, the `routing.models` section uses the following structured schema:

```yaml
routing:
  default_provider: openrouter
  models:
    "<route_id>":
      provider: <provider_name>      # REQUIRED: must exist in 'providers:'
      model: <upstream_model_id>     # REQUIRED: raw model name sent to upstream API
      label: "<Curated Label>"       # OPTIONAL: human-readable name (auto-derived if omitted)
      family: <family_name>          # OPTIONAL: architecture family (auto-derived if omitted)
      strategy: priority             # OPTIONAL: priority | round-robin | weighted
```

### Key Rules

1. **Prefix Route Convention**: The `<route_id>` is namespaced as `<provider>/<model>` (e.g. `nvidia/kimi-k3`, `pccoder/qwen3-coder-30b`, `ollama/llama3.2:3b`).
2. **Provider Enforcement**: Each entry explicitly designates its `provider`. A route cannot exist without a valid provider defined in the `providers:` block.
3. **Upstream Rewriting**: The `model` field is what the upstream provider expects. For example, Nvidia expects `kimi-k3`, while Butter exposes `nvidia/kimi-k3`. Butter automatically translates the request body before transmitting.
4. **Curated Metadata**: `label` and `family` are stored right alongside the route and broadcast to all clients via `/v1/models` and `/api/models`.

---

## 3. The Curated Catalog API (`/v1/models` & `/api/models`)

Both `GET /v1/models` and `GET /api/models` return the enhanced model schema:

```json
{
  "object": "list",
  "data": [
    {
      "id": "nvidia/kimi-k3",
      "object": "model",
      "created": 0,
      "owned_by": "nvidia",
      "owner": "nvidia",
      "label": "Kimi K3",
      "family": "kimi"
    },
    {
      "id": "pccoder/qwen3-coder-30b",
      "object": "model",
      "created": 0,
      "owned_by": "pccoder",
      "owner": "pccoder",
      "label": "Qwen 3 Coder 30B (Local)",
      "family": "qwen"
    },
    {
      "id": "ollama/llama3.2:3b",
      "object": "model",
      "created": 0,
      "owned_by": "ollama",
      "owner": "ollama",
      "label": "Llama 3.2 3B (Local)",
      "family": "llama"
    }
  ]
}
```

### Client Integration Example (JavaScript / TypeScript)

Clients no longer need regex or string manipulation:

```typescript
// Fetch from Butter
const res = await fetch("http://127.0.0.1:8080/api/models");
const { data: models } = await res.json();

// Render dropdown options
models.forEach((m) => {
  console.log(`Option: ${m.label} | Badge: [${m.owner.toUpperCase()}] | Family: ${m.family}`);
  // Wire ID sent to /v1/chat/completions: m.id (e.g. "nvidia/kimi-k3")
});
```

---

## 4. Complete `config.yaml` Example

```yaml
server:
  address: "127.0.0.1:8080"
  read_timeout: 30s
  write_timeout: 120s
  read_header_timeout: 10s
  idle_timeout: 120s
  max_header_bytes: 1048576
  max_request_bytes: 33554432

providers:
  nvidia:
    base_url: https://integrate.api.nvidia.com/v1
    keys:
      - key: "${NVIDIA_API_KEY}"
        weight: 1

  openrouter:
    base_url: https://openrouter.ai/api/v1
    credential_mode: passthrough
    keys:
      - key: "${OPENROUTER_API_KEY}"
        weight: 1

  ollama:
    base_url: http://127.0.0.1:11434/v1
    keys:
      - key: ollama
        weight: 1

  desktop-ollama:
    base_url: http://100.113.98.79:11434/v1
    keys:
      - key: ollama
        weight: 1

  pccoder:
    base_url: http://100.113.98.79:11491/v1
    keys:
      - key: local-only
        weight: 1

  pctalker:
    base_url: http://100.113.98.79:11492/v1
    keys:
      - key: local-only
        weight: 1

routing:
  default_provider: openrouter
  failover:
    enabled: true
    max_retries: 2
    backoff:
      initial: 100ms
      multiplier: 2.0
      max: 2s
    retry_on: [429, 500, 502, 503, 504]

  models:
    # --- NVIDIA ---
    "nvidia/kimi-k3":
      provider: nvidia
      model: kimi-k3
      label: "Kimi K3"
      family: kimi

    "nvidia/glm-5-3":
      provider: nvidia
      model: glm-5-3
      label: "GLM 5.3"
      family: glm

    "nvidia/glm-5-3-flash":
      provider: nvidia
      model: glm-5-3-flash
      label: "GLM 5.3 Flash"
      family: glm

    # --- Local Tailnet Workstation (pccoder / pctalker) ---
    "pccoder/qwen3-coder-30b":
      provider: pccoder
      model: qwen3-coder-30b
      label: "Qwen 3 Coder 30B (Local)"
      family: qwen

    "pctalker/qwen3-14b":
      provider: pctalker
      model: qwen3-14b
      label: "Qwen 3 14B (Local)"
      family: qwen

    # --- Ollama ---
    "ollama/llama3.2:3b":
      provider: ollama
      model: llama3.2:3b
      label: "Llama 3.2 3B (Local)"
      family: llama

    "ollama/qwen3.5:2b":
      provider: ollama
      model: qwen3.5:2b
      label: "Qwen 3.5 2B (Local)"
      family: qwen

    # --- OpenRouter ---
    "openrouter/free":
      provider: openrouter
      model: openrouter/free
      label: "OpenRouter Free"
      family: openrouter
```

---

## 5. Configuration Manager Tool (`butterproxy_config_manager.py`)

A single-file Python tool is provided to manage the configuration locally or over SSH.

### Command Line Interface (CLI)

#### 1. Add or Update a Model Route
```bash
python butterproxy_config_manager.py add-route \
  --provider nvidia \
  --model kimi-k3 \
  --route nvidia/kimi-k3 \
  --label "Kimi K3" \
  --family kimi
```
*If `--route`, `--label`, or `--family` are omitted, they are automatically derived.*

#### 2. List All Active Routes
```bash
python butterproxy_config_manager.py list-routes
```
Output:
```text
ROUTE ID                            PROVIDER        UPSTREAM MODEL                 LABEL                     FAMILY    
-------------------------------------------------------------------------------------------------------------------
nvidia/kimi-k3                      nvidia          kimi-k3                        Kimi K3                   kimi      
nvidia/glm-5-3                      nvidia          glm-5-3                        GLM 5.3                   glm       
pccoder/qwen3-coder-30b             pccoder         qwen3-coder-30b                Qwen 3 Coder 30B (Local)  qwen      
ollama/llama3.2:3b                  ollama          llama3.2:3b                    Llama 3.2 3B (Local)      llama     
```

To output as JSON:
```bash
python butterproxy_config_manager.py list-routes --json
```

#### 3. Remove a Route
```bash
python butterproxy_config_manager.py remove-route --route nvidia/kimi-k3
```

#### 4. Sync Catalogs from Endpoints
Automatically discovers models from all configured providers, namespaces them under `<provider>/<model>`, derives labels and families, and updates `config.yaml`:
```bash
python butterproxy_config_manager.py sync
```

#### 5. Generate a Starter Template
```bash
python butterproxy_config_manager.py generate-template --output config.yaml
```

#### 6. Validate Configuration
```bash
python butterproxy_config_manager.py validate --config config.yaml
```

### Graphical User Interface (GUI)
Launch the desktop GUI:
```bash
python butterproxy_config_manager.py gui
```
Features:
* View and toggle models by provider.
* Configure remote SSH/SFTP connection to `ollama-box` or Tailnet nodes.
* Edit YAML live with validation and automatic service restart.

---

## 6. Build & Deployment

To deploy updates on `ollama-box` or any Linux gateway host:

```bash
# Run tests, compile binary, back up old binary, install to /usr/local/bin/butter, restart systemd
./deploy.sh
```

Or build and test without modifying the running service:
```bash
./deploy.sh --no-deploy
```

### Verification
```bash
# Verify model listing with metadata
curl -s http://127.0.0.1:8080/v1/models | jq .

# Verify chat completion with route rewriting
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nvidia/kimi-k3",
    "messages": [{"role": "user", "content": "Hello!"}]
  }' | jq .
```
