# Butter AI Proxy: Unified Naming Scheme, Multi-Provider Routing & Curated Model Map Guide

## Executive Summary

This document describes the configuration architecture and model registry for **Butter AI Proxy Gateway**. It solves two core architectural problems:

1. **Config Disconnect & Provider Ambiguity**: Previously, provider definitions lived at the top of `config.yaml` while model routes lived with arbitrary IDs, no required provider linkage, and raw JSON pass-through without model rewriting.
2. **Client-Side Name Guessing**: Downstream clients (Letta, chat interfaces, mobile, desktop, IDEs) previously had to parse raw model strings with regex to guess friendly names and providers.

### The Unified Design

* **Every route entry defines its `provider` (or a `providers` failover list) and upstream `model`**.
* **Butter proxy automatically rewrites the outbound request body**, replacing the virtual prefix route with the raw upstream `model` before sending to the upstream provider API.
* **Both `GET /v1/models` and `GET /api/models` emit `{ id, owner, label, family }`**, allowing any client to immediately render clean labels and provider badges with zero string parsing.
* **Support for `extra_body` injection**, allowing per-model Zero Data Retention (ZDR) and custom provider flags to be deep-merged directly into outbound requests.
* **Multi-Provider Failover**: When multiple providers serve the same model (e.g. DeepSeek-V3 on DeepInfra and OpenRouter), a single shared virtual route automatically fails over upon 429 rate limits or 500 errors.
* A dedicated tool (`butterproxy_config_manager.py`) provides automated CLI and GUI management for the schema.

---

## 1. The Architecture

```text
+-------------------------------------------------------------------------+
| Client Applications                                                     |
| (Letta Desktop, Open WebUI, Mobile, Desktop App, Cursor, Python SDK)    |
|                                                                         |
| Reads: GET /api/models or GET /v1/models                                |
| Renders:                                                                |
|   Label:  "GPT-4o Mini (1min.AI)"                                       |
|   Badge:  [ONEMIN] (Family: openai)                                     |
| Sends:    {"model": "onemin/gpt-4o-mini", "messages": [...]}            |
+-------------------------------------------------------------------------+
                                    |
                                    | HTTP :8080
                                    v
+-------------------------------------------------------------------------+
| Butter AI Proxy Gateway                                                 |
|                                                                         |
| 1. Resolves route: "onemin/gpt-4o-mini"                                 |
| 2. Finds target provider: "onemin"                                      |
| 3. Rewrites outbound JSON: "model" -> "gpt-4o-mini"                     |
| 4. Merges extra_body (if configured, e.g. ZDR flags)                    |
| 5. Injects provider auth & headers                                      |
| 6. Dispatches request to provider endpoint                              |
+-------------------------------------------------------------------------+
                                    |
            +-----------------------+-----------------------+
            |                       |                       |
            v                       v                       v
    [1min.AI Gateway]       [DeepInfra / OpenRouter]     [Local Ollama Node]
    https://api.1min.ai/    Cloud inference hubs         http://127.0.0.1:
    openai/v1               Receives:                    11434/v1
    Receives:               {"model":"deepseek-ai/       Receives:
    {"model":"gpt-4o-mini"}  DeepSeek-V3"}               {"model":"llama3.2:3b"}
```

---

## 2. Configuration Schema & Routing Patterns

In `config.yaml`, the `routing.models` section supports two core paradigms:

### Pattern A: Explicit Namespaced Routes (`<provider>/<model>`)
Used when you want downstream clients to explicitly choose the exact provider from their model dropdown list:

```yaml
routing:
  models:
    "onemin/gpt-4o-mini":
      provider: onemin
      model: gpt-4o-mini
      label: "GPT-4o Mini (1min.AI)"
      family: openai
      strategy: priority

    "openrouter/nousresearch/hermes-3-llama-3.1-405b":
      provider: openrouter
      model: nousresearch/hermes-3-llama-3.1-405b
      label: "Hermes 3 405B (ZDR Restricted)"
      family: llama
      strategy: priority
      extra_body:
        provider:
          zdr: true
          data_collection: "deny"
          require_parameters: false
```

### Pattern B: Shared Virtual Model with Multi-Provider Failover
Used when multiple providers host the same model (e.g. DeepSeek-V3), and you want high availability:

```yaml
routing:
  models:
    "deepseek-chat":
      providers: [deepinfra, openrouter]
      model: deepseek-ai/DeepSeek-V3
      label: "DeepSeek V3 (Auto-Failover)"
      family: deepseek
      strategy: priority
```
When a client requests `{"model": "deepseek-chat"}`, Butter attempts `deepinfra`. If DeepInfra returns HTTP 429, 500, or 503, Butter automatically dispatches to `openrouter`, rewriting the upstream model accordingly.

---

## 3. The Curated Catalog API (`/v1/models` & `/api/models`)

Both `GET /v1/models` and `GET /api/models` return the enriched model schema:

```json
{
  "object": "list",
  "data": [
    {
      "id": "onemin/gpt-4o-mini",
      "object": "model",
      "created": 0,
      "owned_by": "onemin",
      "owner": "onemin",
      "label": "GPT-4o Mini (1min.AI)",
      "family": "openai"
    },
    {
      "id": "deepseek-chat",
      "object": "model",
      "created": 0,
      "owned_by": "deepinfra",
      "owner": "deepinfra",
      "label": "DeepSeek V3 (Auto-Failover)",
      "family": "deepseek"
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

### Client Integration Example (TypeScript / JavaScript)

```typescript
// Fetch curated catalog from Butter
const res = await fetch("http://127.0.0.1:8080/api/models");
const { data: models } = await res.json();

// Render dropdown options
models.forEach((m) => {
  console.log(`Option: ${m.label} | Badge: [${m.owner.toUpperCase()}] | Family: ${m.family}`);
  // Wire ID sent to /v1/chat/completions: m.id (e.g. "onemin/gpt-4o-mini" or "deepseek-chat")
});
```

---

## 4. Configuration Manager CLI (`butterproxy_config_manager.py`)

A single-file Python tool is provided to manage the configuration locally or over SSH.

### Command Line Interface (CLI)

#### 1. Add or Update a Model Route
```bash
# Add a single namespaced route
python butterproxy_config_manager.py add-route \
  --provider onemin \
  --model gpt-4o-mini \
  --route onemin/gpt-4o-mini \
  --label "GPT-4o Mini (1min.AI)" \
  --family openai

# Add a multi-provider failover route
python butterproxy_config_manager.py add-route \
  --providers deepinfra openrouter \
  --model deepseek-ai/DeepSeek-V3 \
  --route deepseek-chat \
  --label "DeepSeek V3 (Failover)" \
  --family deepseek

# Add a route with Zero Data Retention (extra_body)
python butterproxy_config_manager.py add-route \
  --provider openrouter \
  --model nousresearch/hermes-3-llama-3.1-405b \
  --extra-body '{"provider": {"zdr": true, "data_collection": "deny"}}'
```

#### 2. Validate Configuration
```bash
python butterproxy_config_manager.py validate --config config.yaml
```

#### 3. List All Configured Routes
```bash
python butterproxy_config_manager.py list-routes --config config.yaml
```

#### 4. Auto-Discover Upstream Models
```bash
python butterproxy_config_manager.py discover \
  --base-url https://api.1min.ai/openai/v1 \
  --api-key-env ONEMIN_AI_API_KEY
```
