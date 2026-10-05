# Butter Proxy 🧈
> A high-performance, multi-provider AI proxy gateway in Go with **explicit provider namespacing**, **shared model multi-provider failover**, **transparent model rewriting**, and a **curated multi-client model catalog**.

[![Go Version](https://img.shields.io/badge/Go-1.25+-00ADD8?style=flat&logo=go)](https://golang.org)
[![Linux Platform](https://img.shields.io/badge/Platform-Linux%20%2F%20Ubuntu-E95420?style=flat&logo=ubuntu)](https://ubuntu.com)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Architecture](https://img.shields.io/badge/Architecture-Headless%20Server%20Daemon-purple.svg)]()

Butter sits between your client applications (Letta Desktop, Open WebUI, Cursor, LibreChat, mobile apps, Python scripts) and multiple upstream LLM providers (1min.AI, OpenRouter, DeepInfra, Anthropic, OpenAI, AWS Bedrock, local Ollama / vLLM / TabbyAPI nodes).

It normalizes disparate endpoints into a single local or LAN gateway (`http://your-server:8080/v1`) with automatic request rewriting, intelligent failover, key management, and rich metadata discovery.

---

## 🌟 Why Butter? Core Capabilities

### 1. Connect Multiple OpenAI-Compatible Endpoints Without Collisions
Most AI apps and web UIs only let you connect to one OpenAI-compatible endpoint at a time. Butter allows you to connect dozens of providers simultaneously—both cloud aggregators and local GPU rigs—under one unified proxy.

### 2. Intelligent Routing When Providers Share the Same Model
When multiple providers host the same model (e.g., DeepSeek-V3 or Llama-3.3-70B served by DeepInfra, OpenRouter, 1min.AI, and local Ollama), Butter gives you two flexible routing patterns:

* **Pattern A: Explicit Provider Namespacing (`<provider>/<model>`)**  
  Exposes distinct entries in client model selectors:
  * `onemin/gpt-4o-mini`
  * `deepinfra/deepseek-ai/DeepSeek-V3`
  * `ollama/llama3.2:3b`  
  Butter intercepts the request, strips the provider prefix, and rewrites `"model"` in the JSON body to the exact upstream ID expected by that provider.

* **Pattern B: Shared Virtual Route with Automatic Failover**  
  Exposes a single clean virtual model to clients (e.g. `deepseek-chat`):
  ```yaml
  routing:
    models:
      "deepseek-chat":
        providers: [deepinfra, openrouter, onemin]
        model: deepseek-ai/DeepSeek-V3
        strategy: priority
  ```
  Clients request `"deepseek-chat"`. Butter tries `deepinfra` first. If DeepInfra returns a `429 Too Many Requests` or `500 Server Error`, Butter automatically fails over to `openrouter`, rewriting the request on the fly!

### 3. Zero Data Retention (ZDR) & Upstream Payload Injection (`extra_body`)
Enforce strict privacy constraints or inject provider-specific routing flags on a per-model basis:
* **Native Go Deep Merging**: Deep-merges arbitrary JSON keys into outgoing request bodies (`internal/proxy/engine.go:MergeExtraBodyInJSON`) using Go's standard library `encoding/json`.
* **Zero Data Retention**: Seamlessly enforce OpenRouter's ZDR privacy headers:
  ```yaml
  extra_body:
    provider:
      zdr: true
      data_collection: "deny"
      require_parameters: false
  ```

### 4. Curated Model Catalog (`/v1/models` & `/api/models`)
Both `/v1/models` and `/api/models` return enriched metadata for every route:
```json
{
  "object": "list",
  "data": [
    {
      "id": "onemin/gpt-4o-mini",
      "object": "model",
      "created": 1728100000,
      "owned_by": "onemin",
      "owner": "onemin",
      "label": "GPT-4o Mini (1min.AI)",
      "family": "openai"
    }
  ]
}
```
Client applications can immediately render human-readable labels (`GPT-4o Mini (1min.AI)`) and provider badges (`onemin`) without hardcoded client-side lookup tables.

---

## 🏛 Architecture

```text
Downstream Clients (Letta Desktop, Open WebUI, Cursor, Mobile, Python SDK)
                           │
             HTTP Requests (http://server:8080/v1)
                           │
                           ▼
          ┌──────────────────────────────────┐
          │      Butter Proxy Daemon         │
          │    (/usr/local/bin/butter)       │
          ├──────────────────────────────────┤
          │ • Namespaced Route Parser        │
          │ • Upstream Model Rewriter        │
          │ • extra_body Payload Injector    │
          │ • Multi-Provider Failover Engine │
          │ • Key Rotation & Health Checks   │
          │ • Curated Catalog (/api/models)  │
          └──────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   1min.AI Gateway    DeepInfra / OpenRouter    Local Nodes
 (Cloud Multi-Model)   (Cloud Aggregators)  (Ollama / vLLM / Tabby)
```

---

## 🚀 Quick Start (Setting Up Butter)

### 1. Build and Install

```bash
# Clone the repository
git clone https://github.com/Flambeau-Phoenix/butter-modified-config.git
cd butter-modified-config

# Build the Linux binary (requires Go 1.25+)
go build -o /usr/local/bin/butter ./cmd/butter/
```

### 2. Choose Your Configuration Starting Point

Butter provides two starter templates:

* **Option A: Blank Canvas (`blank_config.yaml`)**  
  Best if you want a clean empty canvas to define only the providers you use:
  ```bash
  cp blank_config.yaml config.yaml
  ```

* **Option B: Full Reference Template (`config.example.yaml`)**  
  Best if you want pre-configured examples for 1min.AI, OpenRouter (with ZDR), DeepInfra, and local Ollama:
  ```bash
  cp config.example.yaml config.yaml
  ```

* **Option C: Generate with CLI Tool**  
  ```bash
  python3 butterproxy_config_manager.py generate-template --output config.yaml
  ```

---

## ⚙️ Configuration Walkthrough (`config.yaml`)

Here is an example `config.yaml` demonstrating multiple providers and shared model routing:

```yaml
server:
  address: "0.0.0.0:8080"
  read_timeout: 30s
  write_timeout: 120s
  read_header_timeout: 10s
  idle_timeout: 120s
  max_header_bytes: 1048576
  max_request_bytes: 33554432

providers:
  # 1min.AI Gateway (Native OpenAI-compatible endpoint)
  onemin:
    base_url: https://api.1min.ai/openai/v1
    keys:
      - key: "${ONEMIN_AI_API_KEY}"
        weight: 1

  # OpenRouter Cloud Aggregator
  openrouter:
    base_url: https://openrouter.ai/api/v1
    credential_mode: passthrough
    keys:
      - key: "${OPENROUTER_API_KEY}"
        weight: 1

  # DeepInfra Direct API
  deepinfra:
    base_url: https://api.deepinfra.com/v1/openai
    keys:
      - key: "${DEEPINFRA_API_KEY}"
        weight: 1

  # Local Ollama Node
  ollama:
    base_url: http://127.0.0.1:11434/v1
    keys:
      - key: ollama
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
    # --------------------------------------------------------------------------
    # PATTERN 1: Explicit Namespaced Routes (<provider>/<upstream_model>)
    # Client asks for 'onemin/gpt-4o-mini' -> Butter rewrites to 'gpt-4o-mini'
    # --------------------------------------------------------------------------
    "onemin/gpt-4o-mini":
      provider: onemin
      model: gpt-4o-mini
      label: "GPT-4o Mini (1min.AI)"
      family: openai
      strategy: priority

    "onemin/claude-3-5-sonnet":
      provider: onemin
      model: claude-3-5-sonnet
      label: "Claude 3.5 Sonnet (1min.AI)"
      family: claude
      strategy: priority

    # Route with Zero Data Retention enforced on OpenRouter
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

    "ollama/llama3.2:3b":
      provider: ollama
      model: llama3.2:3b
      label: "Llama 3.2 3B (Local)"
      family: llama
      strategy: priority

    # --------------------------------------------------------------------------
    # PATTERN 2: Shared Virtual Route with Multi-Provider Failover
    # Client requests 'deepseek-chat'. Butter tries DeepInfra first; on 429/500
    # it automatically fails over to OpenRouter!
    # --------------------------------------------------------------------------
    "deepseek-chat":
      providers: [deepinfra, openrouter]
      model: deepseek-ai/DeepSeek-V3
      label: "DeepSeek V3 (Multi-Provider Failover)"
      family: deepseek
      strategy: priority
```

---

## 🖥 Running as a Systemd Service

Create `/etc/systemd/system/butter.service`:

```ini
[Unit]
Description=Butter AI Proxy Gateway
After=network.target

[Service]
Type=simple
User=butter
WorkingDirectory=/etc/butter
EnvironmentFile=/etc/butter/.env
ExecStart=/usr/local/bin/butter --config /etc/butter/config.yaml
Restart=on-failure
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now butter
```

---

## 🛠 Configuration Manager CLI (`butterproxy_config_manager.py`)

Manage and validate your configuration directly on the server or over SSH without manual YAML editing:

### 1. Validate Configuration Syntax
```bash
python3 butterproxy_config_manager.py validate --config config.yaml
```

### 2. Discover Models From Any Endpoint
```bash
python3 butterproxy_config_manager.py discover \
  --base-url https://api.1min.ai/openai/v1 \
  --api-key-env ONEMIN_AI_API_KEY
```

### 3. Add a Single Namespaced Route
```bash
python3 butterproxy_config_manager.py add-route \
  --config config.yaml \
  --provider onemin \
  --model gpt-4o-mini \
  --label "GPT-4o Mini (1min.AI)" \
  --family openai
```

### 4. Add a Shared Model Failover Route
```bash
python3 butterproxy_config_manager.py add-route \
  --config config.yaml \
  --providers deepinfra openrouter \
  --model deepseek-ai/DeepSeek-V3 \
  --route deepseek-chat \
  --label "DeepSeek V3 (Failover)" \
  --family deepseek
```

### 5. Add a Route with Zero Data Retention (`extra_body`)
```bash
python3 butterproxy_config_manager.py add-route \
  --config config.yaml \
  --provider openrouter \
  --model nousresearch/hermes-3-llama-3.1-405b \
  --extra-body '{"provider": {"zdr": true, "data_collection": "deny"}}'
```

### 6. List Registered Routes
```bash
python3 butterproxy_config_manager.py list-routes --config config.yaml
```

---

## 🔌 Connecting Downstream Clients

Configure any OpenAI-compatible application to connect through Butter:

| Setting | Value |
| :--- | :--- |
| **API Base URL** | `http://<your-server-ip>:8080/v1` |
| **API Key** | `butter` *(or any arbitrary string)* |
| **Model** | Any virtual route configured in Butter (e.g. `onemin/gpt-4o-mini`, `deepseek-chat`) |

### Example: Python OpenAI SDK
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="butter"
)

response = client.chat.completions.create(
    model="onemin/gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello via Butter!"}],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

---

## 📡 API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/v1/chat/completions` | `POST` | Standard OpenAI chat endpoint with streaming SSE, dynamic model rewriting, and failover |
| `/v1/models` | `GET` | List all available models with enriched `{ id, owner, label, family }` metadata |
| `/api/models` | `GET` | Dedicated alias endpoint for frontend clients and WebUIs |
| `/v1/messages` | `POST` | Anthropic-native Messages API with failover |
| `/healthz` | `GET` | Gateway health status |

---

## 📄 License

Licensed under the [Apache License 2.0](LICENSE).
