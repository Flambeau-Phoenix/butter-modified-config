# Butter Proxy 🧈

> A blazingly fast, multi-provider AI proxy gateway in Go with **explicit provider namespacing**, **automatic payload model rewriting**, and a **curated multi-client model catalog**.

[![Go Version](https://img.shields.io/badge/Go-1.25+-00ADD8?style=flat&logo=go)](https://golang.org)
[![Linux Platform](https://img.shields.io/badge/Platform-Linux%20%2F%20Ubuntu-E95420?style=flat&logo=ubuntu)](https://ubuntu.com)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Architecture](https://img.shields.io/badge/Architecture-Headless%20Server%20Daemon-purple.svg)]()

Butter sits between your applications (web chat, IDE assistants, Open WebUI, mobile clients) and upstream LLM providers (Nvidia NIM, OpenRouter, Anthropic, OpenAI, AWS Bedrock, local Ollama/vLLM/TabbyAPI nodes). It normalizes disparate APIs into a unified, high-performance gateway with failover, key rotation, and metadata discovery.

---

## 🌟 What's New in This Release

### 1. Explicit Provider-Model Routing Scheme
Say goodbye to ambiguous route declarations. Every route in `config.yaml` explicitly couples the incoming virtual route to an upstream provider and upstream model ID:

```yaml
routes:
  nvidia/kimi-k3:
    provider: nvidia
    model: moonshotai/kimi-k3
    label: "Kimi K3"
    family: kimi

  openrouter/claude-3.5-sonnet:
    provider: openrouter
    model: anthropic/claude-3.5-sonnet
    label: "Claude 3.5 Sonnet"
    family: claude
```

### 2. Transparent Upstream Model Rewriting
When a client requests `nvidia/kimi-k3`, Butter dynamically intercepts the request and rewrites the JSON payload's `"model"` parameter to `moonshotai/kimi-k3` before dispatching to Nvidia's API.
* **Non-streaming & Streaming SSE**: Fully supported with zero latency overhead.
* **Anthropic Native**: Native message endpoints rewrite model strings seamlessly.

### 3. Curated Model Catalog (`/v1/models` & `/api/models`)
Both `/v1/models` and `/api/models` return enriched metadata for every route:

```json
{
  "object": "list",
  "data": [
    {
      "id": "nvidia/kimi-k3",
      "object": "model",
      "created": 1774760000,
      "owned_by": "nvidia",
      "owner": "nvidia",
      "label": "Kimi K3",
      "family": "kimi"
    }
  ]
}
```
* **Frontend-Ready**: Client applications (Studio, WebUI, Chat, Mobile) can immediately render human-readable labels (`Kimi K3`) and provider badges (`nvidia`) without hardcoded client-side lookup tables.

### 4. Headless Server Config Manager (`butterproxy_config_manager.py`)
A single-file, dependency-free Python tool built to manage your gateway over SSH without requiring an X11/desktop environment:
* Audits and lists all registered routes.
* Programmatically adds or removes model routes with validation.
* Automatically discovers and syncs newly pulled Ollama or local worker models.
* Detects display capabilities: launches a desktop GUI if available, or gracefully provides CLI subcommands and `--help` on headless Linux servers.

---

## 🏛 Architecture

```
Downstream Clients (Chat UIs, IDEs, Mobile, Open WebUI)
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
          │ • Token-Bucket Rate Limiter      │
          │ • Key Rotation & Health Checks   │
          │ • Curated Catalog (/api/models)  │
          └──────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   Nvidia NIM         OpenRouter         Local Workers
(Cloud Inference)  (Multi-Model Hub)  (Ollama / vLLM / Tabby)
```

---

## 🚀 Quick Start (Ubuntu Server)

Butterproxy is designed to run as a native Linux daemon on your headless server.

### 1. Build and Install

```bash
# Clone the repository
git clone https://github.com/your-username/butter.git
cd butter

# Build the Linux binary
go build -o /usr/local/bin/butter ./cmd/butter/
```

### 2. Configure

Copy the reference template and configure your provider keys and routes:

```bash
cp config.example.yaml /home/flambeau/butter/config.yaml
```

Example `config.yaml`:
```yaml
server:
  address: ":8080"
  read_timeout: 30s
  write_timeout: 120s

providers:
  nvidia:
    type: openai
    base_url: https://integrate.api.nvidia.com/v1
    api_key: ${NVIDIA_API_KEY}
  ollama:
    type: openai
    base_url: http://127.0.0.1:11434/v1

routes:
  nvidia/kimi-k3:
    provider: nvidia
    model: moonshotai/kimi-k3
    label: "Kimi K3"
    family: kimi

  ollama/qwen2.5-coder:
    provider: ollama
    model: qwen2.5-coder:32b
    label: "Qwen 2.5 Coder 32B"
    family: qwen
```

### 3. Systemd Service

Create `/etc/systemd/system/butter.service`:

```ini
[Unit]
Description=Butter AI Proxy Gateway
After=network.target

[Service]
Type=simple
User=flambeau
WorkingDirectory=/home/flambeau/butter
ExecStart=/usr/local/bin/butter -config /home/flambeau/butter/config.yaml
Restart=always
RestartSec=5s
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

## 🛠 Config Manager CLI Usage

Run `butterproxy_config_manager.py` directly on your server or via SSH:

### List Configured Routes
```bash
python3 butterproxy_config_manager.py list-routes --config config.yaml
```
Output:
```
ROUTE ID                            PROVIDER        UPSTREAM MODEL                 LABEL                     FAMILY    
-------------------------------------------------------------------------------------------------------------------
nvidia/kimi-k3                      nvidia          moonshotai/kimi-k3             Kimi K3                   kimi      
openrouter/claude-3.5-sonnet        openrouter      anthropic/claude-3.5-sonnet    Claude 3.5 Sonnet         claude    
ollama/qwen2.5-coder                ollama          qwen2.5-coder:32b              Qwen 2.5 Coder 32B        qwen      
```

### Add a Route
```bash
python3 butterproxy_config_manager.py add-route \
  --route nvidia/deepseek-r1 \
  --provider nvidia \
  --model deepseek-ai/deepseek-r1 \
  --label "DeepSeek R1" \
  --family deepseek \
  --config config.yaml
```

### Validate Configuration
```bash
python3 butterproxy_config_manager.py validate --config config.yaml
```

### One-Click Server Deployment (`deploy.sh`)
```bash
chmod +x deploy.sh
./deploy.sh
```
`deploy.sh` automatically creates a timestamped backup of `config.yaml`, runs Go unit tests, builds the binary, and gracefully restarts `butter.service`.

---

## 📡 API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/v1/chat/completions` | `POST` | Standard OpenAI chat endpoint with streaming SSE support and model rewriting |
| `/v1/models` | `GET` | List all available models with enriched `{ id, owner, label, family }` metadata |
| `/api/models` | `GET` | Alias endpoint for frontend clients and WebUIs |
| `/v1/messages` | `POST` | Anthropic-native Messages API with failover |
| `/healthz` | `GET` | Gateway health status |

---

## 📄 License

Licensed under the [Apache License 2.0](LICENSE).
