<h1 align="center">Butter Config Manager</h1>

<p align="center">
  <strong>A config toolkit for the Butter AI proxy gateway: multi-provider routing, model discovery, failover and a curated model catalog, managed from a CLI or desktop GUI.</strong>
</p>

<p align="center">
  <a href="https://github.com/Flambeau-Phoenix/butter-modified-config/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Flambeau-Phoenix/butter-modified-config/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache%202.0-blue.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue.svg">
</p>

[Butter](https://github.com/Temikus/butter) is a Go AI proxy gateway that gives your apps one
OpenAI-compatible endpoint in front of many model providers. Writing and maintaining its
YAML by hand gets tedious once you have several providers and dozens of models. This
repository provides:

- **`butterproxy_config_manager.py`**, a single-file CLI and Tk desktop GUI that discovers
  models from any OpenAI-compatible endpoint, maintains `providers` and `routing.models`,
  validates the result, and can edit configs on remote servers over SSH.
- **Ready-to-copy config templates** (`blank_config.yaml`, `config.example.yaml`).
- **A naming and model-map guide** explaining the routing scheme.
- **`deploy.sh`**, which validates a config and installs it with a Butter binary.

> ### Read this first: how this relates to upstream Butter
>
> This repository **does not contain the Butter proxy**. It manages configuration for it.
>
> The schema this toolkit writes goes beyond upstream Butter's example config. In
> particular, per-route `label`, `family`, `extra_body`, `context_length` and
> `max_output_tokens`, and the enriched `/api/models` catalog, are **not in upstream Butter
> as of v0.6.3** (none appear in its source or example config). They require a Butter build
> that implements them, and **that modified build is not part of this repository**.
>
> - With a build that supports the extensions, everything below applies.
> - With stock upstream Butter, stick to the standard keys (`server`, `providers`,
>   `routing.default_provider`, `routing.failover`, and routes with `provider(s)` and
>   `model`). Extended keys may be ignored or rejected. Check against your version.
>
> Butter is by [Temikus](https://github.com/Temikus/butter) and licensed Apache-2.0. This
> project is independent and not affiliated with or endorsed by its authors.

**Contents:**
[What the extended schema adds](#what-the-extended-schema-adds) ·
[Quick start](#quick-start) ·
[Configuration](#configuration) ·
[Config manager reference](#config-manager-reference) ·
[Deploying](#deploying) ·
[Connecting clients](#connecting-clients) ·
[Security](#security) ·
[Repository layout](#repository-layout) ·
[Development](#development)

## What the extended schema adds

### Provider-namespaced routes

Many apps can only connect to one OpenAI-compatible endpoint. With namespaced routes you
expose several providers through one gateway and tell them apart in the model picker:
`onemin/gpt-4o-mini`, `deepinfra/deepseek-ai/DeepSeek-V3`, `ollama/llama3.2:3b`. The proxy
strips the prefix and rewrites `"model"` in the request body to the upstream's exact ID.

### Shared routes with failover

Expose one clean model name that several providers can serve, tried in priority order:

```yaml
routing:
  models:
    "deepseek-chat":
      providers: [deepinfra, openrouter, onemin]
      model: deepseek-ai/DeepSeek-V3
      strategy: priority
```

Clients ask for `deepseek-chat`. On a retryable status (for example `429` or `5xx`) the
proxy moves to the next provider and rewrites the request for it.

### Per-model `extra_body`

Deep-merge arbitrary JSON into the outgoing request for a route. A common use is
forwarding OpenRouter's zero-data-retention preferences:

```yaml
extra_body:
  provider:
    zdr: true
    data_collection: "deny"
    require_parameters: false
```

This forwards the flags; whether data is actually retained is decided by the upstream
provider, so check their terms.

### Curated catalog and model limits

Routes carry `label`, `family`, and optional `context_length` and `max_output_tokens`.
`/v1/models` and `/api/models` return them so client apps can show clean names and size
prompts correctly:

```json
{ "id": "onemin/gpt-4o-mini", "owned_by": "onemin", "label": "GPT-4o Mini (1min.AI)",
  "family": "openai", "context_length": 128000, "max_output_tokens": 16384 }
```

Omit the limits to let the proxy resolve them from the upstream; set them to enforce a
lower window. Full explanation: [BUTTER_NAMING_AND_MODEL_MAP_GUIDE.md](BUTTER_NAMING_AND_MODEL_MAP_GUIDE.md).

## Quick start

Requirements: Python 3.10+.

```bash
git clone https://github.com/Flambeau-Phoenix/butter-modified-config.git
cd butter-modified-config
pip install -r requirements.txt
```

**1. Start from a template**

```bash
cp blank_config.yaml config.yaml            # clean canvas: define only what you use
# or
cp config.example.yaml config.yaml          # worked examples: 1min.AI, OpenRouter (ZDR), DeepInfra, Ollama
# or
python butterproxy_config_manager.py generate-template --output config.yaml
```

**2. Add providers and routes.** Put API keys in environment variables, not in the file
(`key: "${OPENROUTER_API_KEY}"`), then let the tool find models for you:

```bash
export ONEMIN_AI_API_KEY=...                 # never commit real keys
python butterproxy_config_manager.py discover \
  --base-url https://api.1min.ai/openai/v1 --api-key-env ONEMIN_AI_API_KEY
python butterproxy_config_manager.py add-route --config config.yaml \
  --provider onemin --model gpt-4o-mini --label "GPT-4o Mini (1min.AI)" --family openai
```

**3. Validate and deploy**

```bash
python butterproxy_config_manager.py validate --config config.yaml
./deploy.sh config.yaml
```

Prefer a window? `python butterproxy_config_manager.py gui` opens the desktop manager
(needs Tkinter, which ships with most Python desktop installs).

## Configuration

A trimmed `config.yaml` with two providers, a namespaced route and a failover route:

```yaml
server:
  address: "0.0.0.0:8080"          # bind to 127.0.0.1:8080 unless you want LAN access

providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    credential_mode: passthrough
    keys:
      - key: "${OPENROUTER_API_KEY}"
        weight: 1
  deepinfra:
    base_url: https://api.deepinfra.com/v1/openai
    keys:
      - key: "${DEEPINFRA_API_KEY}"
        weight: 1

routing:
  default_provider: openrouter
  failover:
    enabled: true
    max_retries: 2
    backoff: { initial: 100ms, multiplier: 2.0, max: 2s }
    retry_on: [429, 500, 502, 503, 504]
  models:
    "openrouter/nousresearch/hermes-3-llama-3.1-405b":    # namespaced route
      provider: openrouter
      model: nousresearch/hermes-3-llama-3.1-405b
      label: "Hermes 3 405B (ZDR)"
      family: llama
      extra_body: { provider: { zdr: true, data_collection: "deny" } }
    "deepseek-chat":                                      # shared route with failover
      providers: [deepinfra, openrouter]
      model: deepseek-ai/DeepSeek-V3
      label: "DeepSeek V3 (failover)"
      family: deepseek
      strategy: priority
```

See [`config.example.yaml`](config.example.yaml) for a complete, validated example.

## Config manager reference

```bash
python butterproxy_config_manager.py <command> [options]
```

| Command | What it does |
| --- | --- |
| `gui` | Open the desktop configuration manager (local files, remote servers over SSH/SFTP, presets) |
| `discover` | List model IDs from one endpoint (`--base-url`, `--api-key-env`) |
| `sync` | Refresh `routing.models` from your providers (`--dry-run`, `--no-prune`, `--provider`) |
| `add-route` | Add or update a route: `--provider` or `--providers`, `--model`, `--route`, `--label`, `--family`, `--context-length`, `--max-output-tokens`, `--strategy`, `--extra-body` |
| `list-routes` | List configured routes and their metadata |
| `remove-route` | Remove a route (`--route`) |
| `summary` | Print a config summary with secrets redacted |
| `validate` | Check configuration syntax and schema |
| `generate-template` | Write a clean starter config |

Most commands take `--config` (default: `/etc/butter/config.yaml` if present, otherwise
`./config.yaml`). Examples:

```bash
# Shared model with failover across two providers
python butterproxy_config_manager.py add-route --config config.yaml \
  --providers deepinfra openrouter --model deepseek-ai/DeepSeek-V3 \
  --route deepseek-chat --label "DeepSeek V3 (Failover)" --family deepseek

# Route with zero-data-retention preferences forwarded to OpenRouter
python butterproxy_config_manager.py add-route --config config.yaml \
  --provider openrouter --model nousresearch/hermes-3-llama-3.1-405b \
  --extra-body '{"provider": {"zdr": true, "data_collection": "deny"}}'

# Preview what a sync would change
python butterproxy_config_manager.py sync --config config.yaml --dry-run
```

Remote editing over SSH needs the optional `paramiko` package
(`pip install paramiko`).

## Deploying

`deploy.sh` validates the config, then finds a Butter binary in this order: `./cmd/butter`
source, `$BUTTER_SRC`, a sibling `../butter-src` checkout (each is built and installed to
`/usr/local/bin/butter` with `sudo`, backing up any existing binary), an already installed
`/usr/local/bin/butter`, or `butter` on your `PATH`. If a `butter.service` systemd unit
exists it restarts it; otherwise it prints how to run Butter manually. It does **not** copy
your config anywhere, so place it where your service expects it (for example
`/etc/butter/config.yaml`).

```bash
./deploy.sh                      # deploy ./config.yaml
./deploy.sh /path/to/config.yaml
```

Note that building from the upstream repository
(`git clone https://github.com/Temikus/butter.git ../butter-src`) gives you **stock
upstream Butter**, which does not include the extensions described above.

An example `systemd` unit:

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

## Connecting clients

Point any OpenAI-compatible app at the gateway:

| Setting | Value |
| --- | --- |
| API base URL | `http://<your-server>:8080/v1` |
| API key | any string, unless you have configured gateway keys |
| Model | a route ID, for example `onemin/gpt-4o-mini` or `deepseek-chat` |

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="butter")
stream = client.chat.completions.create(
    model="onemin/gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello via Butter!"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

Endpoints served by a build with the extensions: `POST /v1/chat/completions`,
`GET /v1/models`, `GET /api/models`, `POST /v1/messages` (Anthropic format) and
`GET /healthz`. Stock upstream Butter documents its own endpoint list.

## Security

- **Keep keys out of files and history.** Reference environment variables
  (`"${NAME}"`) in config. `--api-key` on the command line ends up in your shell history;
  prefer `--api-key-env`. `config.yaml`, `.env` files and key files are git-ignored here.
- **Bind deliberately.** The examples listen on `0.0.0.0:8080` so a LAN can reach the
  gateway. Use `127.0.0.1` for local-only use, and put anything exposed behind a firewall,
  VPN or authenticating reverse proxy, since the gateway forwards requests with your
  provider keys.
- **Review `extra_body`.** It is merged verbatim into upstream requests.
- Report vulnerabilities privately: see [SECURITY.md](SECURITY.md).

## Repository layout

```text
butterproxy_config_manager.py          CLI + desktop GUI config manager (single file)
blank_config.yaml                      Minimal starter config
config.example.yaml                    Worked example with several providers
BUTTER_NAMING_AND_MODEL_MAP_GUIDE.md   Routing scheme and curated model map
deploy.sh                              Validate and install a config with a Butter binary
test_model_limits.py                   Tests for limits, routes and validation
requirements.txt                       Python dependencies
```

## Development

```bash
pip install -r requirements.txt
python -m unittest test_model_limits -v
python butterproxy_config_manager.py validate --config config.example.yaml
```

CI runs these on Linux and Windows. Contributions are welcome: see
[CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md). Release
notes: [CHANGELOG.md](CHANGELOG.md).

## License and credits

[Apache-2.0](LICENSE). Butter is by [Temikus](https://github.com/Temikus/butter)
(Apache-2.0). 1min.AI, OpenRouter, DeepInfra and other providers named here are
trademarks of their owners and are used only to describe compatibility.
