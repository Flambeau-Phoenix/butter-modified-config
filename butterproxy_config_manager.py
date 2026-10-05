#!/usr/bin/env python3
"""Butterproxy Config Manager.

One-file GUI and CLI for discovering OpenAI-compatible models, maintaining
Butter's native ``providers`` + ``routing.models`` schema, and managing local
or remote configurations over SSH/SFTP.

Runtime dependencies: PyYAML, requests. Optional remote support: paramiko.
Tkinter is provided by most Python desktop installations.
"""

from __future__ import annotations

import argparse
import copy
import getpass
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import sys
import tempfile
import threading
import time
from typing import Any, Callable
import uuid

try:
    import requests
except ImportError:  # pragma: no cover - handled at runtime
    requests = None

try:
    import yaml
except ImportError:  # pragma: no cover - handled at runtime
    yaml = None

try:
    import paramiko
except ImportError:  # pragma: no cover - remote support is optional
    paramiko = None


APP_NAME = "Butterproxy Config Manager"
VERSION = "1.1.0"
DEFAULT_CONFIG_PATH = (
    Path("/home/flambeau/butter/config.yaml")
    if Path("/home/flambeau/butter/config.yaml").exists()
    else (
        Path("/etc/butter/config.yaml")
        if Path("/etc/butter/config.yaml").exists()
        else Path("config.yaml")
    )
)
DEFAULT_ENV_PATH = (
    Path("/opt/eh-stack/config/eh.env")
    if Path("/opt/eh-stack/config/eh.env").exists()
    else Path(".env")
)
STATE_DIR = Path.home() / ".config" / "butterproxy-config-manager"
ENDPOINTS_PATH = STATE_DIR / "endpoints.json"
SSH_PROFILES_PATH = STATE_DIR / "ssh-profiles.json"
ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROVIDER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SERVICE_NAME = re.compile(r"^[A-Za-z0-9_.@-]+$")


def extract_env_name(value: Any) -> str:
    """Extract clean environment variable name from 'VAR', '$VAR', or '${VAR}'."""
    if not isinstance(value, str):
        return ""
    clean = value.strip()
    if clean.startswith("${") and clean.endswith("}"):
        clean = clean[2:-1].strip()
    if clean.startswith("$"):
        clean = clean[1:].strip()
    return clean if ENV_NAME.fullmatch(clean) else ""


def format_env_ref(var_name: str) -> str:
    """Format canonical Butter environment variable reference '${VAR}'."""
    clean = extract_env_name(var_name)
    return f"${{{clean}}}" if clean else var_name.strip()


# Butter proxy binary switches strictly on these native provider names
VALID_BUTTER_PROVIDERS = {
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "bedrock",
    "groq",
    "mistral",
    "together",
    "fireworks",
    "perplexity",
    "azureopenai",
}

# Maps common aliases to canonical provider names (custom OpenAI endpoints maintain their distinct identity)
PROVIDER_TYPE_MAP = {
    "local-ollama": "ollama",
    "kilo-gateway": "kilo",
    "opencode-go": "opencode",
    "lm-studio": "lmstudio",
    "together-ai": "together",
}

ENDPOINT_PRESETS = [
    ("Local Ollama", "http://127.0.0.1:11434/v1", "ollama", "none"),
    ("OpenCode Go", "https://opencode.ai/zen/go/v1", "opencode", "OPENCODE_API_KEY"),
    ("Kilo Gateway", "https://api.kilo.ai/api/gateway/v1", "kilo", "KILO_API_KEY"),
    ("OpenRouter", "https://openrouter.ai/api/v1", "openrouter", "OPENROUTER_API_KEY"),
    ("OpenAI", "https://api.openai.com/v1", "openai", "OPENAI_API_KEY"),
    ("Groq", "https://api.groq.com/openai/v1", "groq", "GROQ_API_KEY"),
    ("Together AI", "https://api.together.xyz/v1", "together", "TOGETHER_API_KEY"),
    ("DeepSeek", "https://api.deepseek.com/v1", "deepseek", "DEEPSEEK_API_KEY"),
]

DEFAULT_SERVER_BLOCK = {
    "address": ":8080",
    "read_timeout": "30s",
    "write_timeout": "120s",
    "read_header_timeout": "10s",
    "idle_timeout": "120s",
    "max_header_bytes": 1048576,
    "max_request_bytes": 33554432,
}

DEFAULT_FAILOVER_BLOCK = {
    "enabled": True,
    "max_retries": 2,
    "backoff": {
        "initial": "100ms",
        "multiplier": 2.0,
        "max": "2s",
    },
    "retry_on": [429, 500, 502, 503, 504],
}

DEFAULT_CONFIG_TEMPLATE = """# Butter AI Proxy Gateway Configuration
# Clean unified naming schema: each route entry requires a provider (or providers list), prefix route, and upstream model.

server:
  address: "127.0.0.1:8080"
  read_timeout: 30s
  write_timeout: 120s
  read_header_timeout: 10s
  idle_timeout: 120s
  max_header_bytes: 1048576
  max_request_bytes: 33554432

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

  onemin:
    base_url: https://api.1min.ai/openai/v1
    keys:
      - key: "${ONEMIN_AI_API_KEY}"
        weight: 1

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
    # Pattern 1: Namespaced Routes (<provider>/<model>)
    # Butter rewrites the model parameter to upstream model ID before dispatch.
    # --------------------------------------------------------------------------
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

    "ollama/llama3.2:3b":
      provider: ollama
      model: llama3.2:3b
      label: "Llama 3.2 3B (Local)"
      family: llama

    # --------------------------------------------------------------------------
    # Pattern 2: Shared Virtual Route with Multi-Provider Failover
    # Downstream clients request 'deepseek-chat'. Butter tries DeepInfra first;
    # if it returns 429/500, it automatically fails over to OpenRouter.
    # --------------------------------------------------------------------------
    "deepseek-chat":
      providers: [deepinfra, openrouter]
      model: deepseek-ai/DeepSeek-V3
      label: "DeepSeek V3 (Auto-Failover)"
      family: deepseek
      strategy: priority
"""


class ButterConfigError(RuntimeError):
    """Configuration, discovery, or transport error safe to display to users."""


def require_core_dependencies() -> None:
    missing = []
    if yaml is None:
        missing.append("PyYAML")
    if requests is None:
        missing.append("requests")
    if missing:
        raise ButterConfigError(
            f"Missing dependencies: {', '.join(missing)}. "
            "Install with: python -m pip install PyYAML requests paramiko"
        )


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return copy.deepcopy(default)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n", backup=False)


def load_env_text(text: str, include_process: bool = True) -> dict[str, str]:
    values = dict(os.environ) if include_process else {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env_file(path: Path) -> dict[str, str]:
    try:
        return load_env_text(path.read_text(encoding="utf-8"))
    except OSError:
        return dict(os.environ)


def update_env_text(text: str, key: str, value: str) -> str:
    clean_key = extract_env_name(key)
    if not clean_key:
        raise ButterConfigError(f"Environment variable name {key!r} is invalid")
    key = clean_key
    replacement = f"{key}={value}"
    lines = text.replace("\r\n", "\n").splitlines()
    output: list[str] = []
    replaced = False
    for line in lines:
        if "=" in line and line.split("=", 1)[0].strip() == key:
            output.append(replacement)
            replaced = True
        else:
            output.append(line)
    if not replaced:
        if output and output[-1]:
            output.append("")
        output.append(replacement)
    return "\n".join(output).rstrip() + "\n"


def resolve_key(value: Any, env: dict[str, str]) -> str:
    if not isinstance(value, str):
        return ""
    clean = value.strip()
    var_name = extract_env_name(clean)
    if var_name and var_name in env:
        return env[var_name]
    if clean in env:
        return env[clean]
    match = ENV_REF.fullmatch(clean)
    if match:
        return env.get(match.group(1), "")
    return clean


def public_key_reference(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    clean = value.strip()
    var_name = extract_env_name(clean)
    if var_name:
        return f"${{{var_name}}}"
    if clean in {"ollama", "none"}:
        return clean
    return "configured"


def atomic_write_text(
    path: Path,
    content: str,
    *,
    backup: bool = True,
    backup_suffix: str = ".bak",
) -> Path | None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    mode = 0o600
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
        if backup:
            backup_path = Path(str(path) + backup_suffix)
            shutil.copy2(path, backup_path)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return backup_path


def normalize_provider_name(name: str) -> str:
    clean = name.strip().lower()
    return PROVIDER_TYPE_MAP.get(clean, clean)


def validate_provider_name(name: str, allow_custom: bool = True) -> str:
    raw = name.strip()
    if not PROVIDER_NAME.fullmatch(raw):
        raise ButterConfigError(
            "Provider name must start with a letter or number and contain only "
            "letters, numbers, dots, underscores, or hyphens"
        )
    normalized = normalize_provider_name(raw)
    if not allow_custom and normalized not in VALID_BUTTER_PROVIDERS:
        valid_list = ", ".join(sorted(VALID_BUTTER_PROVIDERS))
        raise ButterConfigError(
            f"Provider {raw!r} is not recognized by Butter. Native provider types: "
            f"[{valid_list}], or custom OpenAI-compatible endpoints (e.g. 'opencode', 'kilo', 'ollama')."
        )
    return normalized


def validate_base_url(base_url: str) -> str:
    clean = base_url.strip().rstrip("/")
    if not clean.startswith(("http://", "https://")):
        raise ButterConfigError("Base URL must start with http:// or https://")
    return clean


def load_config_text(text: str) -> dict[str, Any]:
    require_core_dependencies()
    try:
        config = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ButterConfigError(f"Invalid YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise ButterConfigError("Config root must be a mapping")
    return config


def normalize_route_providers(route: dict[str, Any]) -> list[str]:
    if not isinstance(route, dict):
        return []
    providers = route.get("providers")
    if isinstance(providers, list):
        return [str(p) for p in providers if p]
    prov = route.get("provider")
    if prov:
        return [str(prov)]
    return []


def derive_model_label(model_id: str) -> str:
    clean = model_id.split("/")[-1].replace(":free", "")
    words = [w.capitalize() for w in re.split(r"[-_.]+", clean) if w]
    return " ".join(words) if words else model_id


def derive_model_family(model_id: str) -> str:
    low = model_id.lower()
    for fam in ("qwen", "llama", "gemma", "kimi", "glm", "deepseek", "mistral", "claude", "gpt", "dots", "liquid", "cohere", "phi", "openrouter"):
        if fam in low:
            return fam
    return "other"


def normalize_route_and_upstream(provider: str, model_id: str) -> tuple[str, str]:
    """Derive canonical (route_id, upstream_model) for a provider and model string.

    Prevents doubled-up namespaces (e.g. openrouter/openrouter/free, groq/groq/llama),
    handles OpenRouter native router models (openrouter/free, openrouter/auto),
    and ensures the upstream model sent to the API matches what the provider expects.
    """
    prov = str(provider).strip()
    raw = str(model_id).strip()

    # Strip repeated self-prefixing (e.g. openrouter/openrouter/free -> openrouter/free)
    double = f"{prov}/{prov}/"
    while raw.startswith(double):
        raw = f"{prov}/" + raw[len(double):]

    if prov == "openrouter":
        if raw in {"openrouter/free", "free"}:
            return ("openrouter/free", "openrouter/free")
        if raw in {"openrouter/auto", "auto"}:
            return ("openrouter/auto", "openrouter/auto")
        if raw.startswith("openrouter/"):
            rest = raw.removeprefix("openrouter/")
            if rest in {"free", "auto"}:
                return (f"openrouter/{rest}", f"openrouter/{rest}")
            return (f"openrouter/{rest}", rest)
        else:
            return (f"openrouter/{raw}", raw)

    if raw.startswith(f"{prov}/"):
        upstream = raw.removeprefix(f"{prov}/")
    else:
        upstream = raw

    route_id = f"{prov}/{upstream}"
    return (route_id, upstream)


def validate_butter_config(config: dict[str, Any], strict: bool = True) -> None:
    providers = config.get("providers")
    if not isinstance(providers, dict):
        raise ButterConfigError("Butter config must contain a providers mapping")
    routing = config.get("routing")
    if strict and (
        not isinstance(routing, dict) or not isinstance(routing.get("models"), dict)
    ):
        raise ButterConfigError("Butter config must contain routing.models mapping")
    for name, provider in providers.items():
        validate_provider_name(str(name), allow_custom=True)
        if not isinstance(provider, dict):
            raise ButterConfigError(f"Provider {name!r} must be a mapping")
        validate_base_url(str(provider.get("base_url") or ""))

    if strict:
        server = config.get("server")
        if server is not None and not isinstance(server, dict):
            raise ButterConfigError("Butter config server section must be a mapping")
        if routing and isinstance(routing, dict):
            default_provider = routing.get("default_provider")
            if default_provider and default_provider not in providers:
                normalized_default = normalize_provider_name(str(default_provider))
                if normalized_default not in providers:
                    raise ButterConfigError(
                        f"Default provider {default_provider!r} is not defined in providers mapping"
                    )
            models = routing.get("models")
            if isinstance(models, dict):
                for model_id, route in models.items():
                    if isinstance(route, dict):
                        route_providers = normalize_route_providers(route)
                        if not route_providers:
                            raise ButterConfigError(
                                f"Model route {model_id!r} must specify 'provider' or 'providers'"
                            )
                        for p in route_providers:
                            norm_p = normalize_provider_name(str(p))
                            if str(p) not in providers and norm_p not in providers:
                                raise ButterConfigError(
                                    f"Model route {model_id!r} references undefined provider {p!r}"
                                )
                        extra_body = route.get("extra_body")
                        if extra_body is not None:
                            if not isinstance(extra_body, dict):
                                raise ButterConfigError(
                                    f"Model route {model_id!r} extra_body must be a mapping"
                                )
                            if "model" in extra_body:
                                raise ButterConfigError(
                                    f"Model route {model_id!r} extra_body cannot override 'model'"
                                )


def load_config_file(path: Path) -> dict[str, Any]:
    try:
        config = load_config_text(path.expanduser().read_text(encoding="utf-8"))
    except OSError as exc:
        raise ButterConfigError(f"Cannot read {path}: {exc}") from exc
    validate_butter_config(config, strict=False)
    return config


def dump_config(config: dict[str, Any]) -> str:
    require_core_dependencies()
    ordered: dict[str, Any] = {}

    # 1. Server block (address :8080 and standard timeouts)
    server = config.get("server")
    if isinstance(server, dict):
        server_copy = copy.deepcopy(server)
        if "address" not in server_copy:
            server_copy["address"] = ":8080"
        ordered["server"] = server_copy
    else:
        ordered["server"] = copy.deepcopy(DEFAULT_SERVER_BLOCK)

    # 2. Providers block
    ordered["providers"] = config.get("providers", {})

    # 3. Routing block (ordered with default_provider, failover, models)
    routing = copy.deepcopy(config.get("routing", {})) if isinstance(config.get("routing"), dict) else {}
    if "failover" not in routing:
        routing["failover"] = copy.deepcopy(DEFAULT_FAILOVER_BLOCK)

    ordered_routing: dict[str, Any] = {}
    if "default_provider" in routing:
        ordered_routing["default_provider"] = routing["default_provider"]
    if "failover" in routing:
        ordered_routing["failover"] = routing["failover"]
    if "models" in routing:
        ordered_routing["models"] = routing["models"]
    for k, v in routing.items():
        if k not in ordered_routing:
            ordered_routing[k] = v
    ordered["routing"] = ordered_routing

    # 4. Any other top-level keys
    for k, v in config.items():
        if k not in ordered:
            ordered[k] = v

    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True)


def get_routes(config: dict[str, Any], create: bool = False) -> dict[str, Any]:
    routing = config.get("routing")
    if not isinstance(routing, dict):
        if not create:
            return {}
        routing = {}
        config["routing"] = routing
    routes = routing.get("models")
    if not isinstance(routes, dict):
        if not create:
            return {}
        routes = {}
        routing["models"] = routes
    return routes


def provider_models(config: dict[str, Any], provider_name: str) -> list[str]:
    result = []
    for route_id, route in get_routes(config).items():
        if isinstance(route, dict):
            providers = normalize_route_providers(route)
            if provider_name in providers:
                upstream = route.get("model")
                if not upstream or not str(upstream).strip():
                    _, upstream = normalize_route_and_upstream(provider_name, str(route_id))
                else:
                    _, upstream = normalize_route_and_upstream(provider_name, str(upstream))
                result.append(str(upstream).strip())
    return sorted(set(result))


def first_provider_key(provider: dict[str, Any]) -> str:
    if not isinstance(provider, dict):
        return ""
    keys = provider.get("keys") if "keys" in provider else provider.get("Keys")
    if isinstance(keys, list) and keys:
        first = keys[0]
        if isinstance(first, dict):
            val = first.get("key") or first.get("Key")
            if val is not None:
                return str(val)
        elif isinstance(first, str):
            return first
    if provider.get("api_key"):
        return str(provider["api_key"])
    if provider.get("key"):
        return str(provider["key"])
    return ""


def config_summary(config: dict[str, Any]) -> dict[str, Any]:
    providers = config.get("providers")
    providers = providers if isinstance(providers, dict) else {}
    routing = config.get("routing")
    routing = routing if isinstance(routing, dict) else {}
    default_provider = str(routing.get("default_provider") or "")
    items = []
    for name, raw_provider in providers.items():
        provider = raw_provider if isinstance(raw_provider, dict) else {}
        models = provider_models(config, str(name))
        items.append(
            {
                "name": str(name),
                "base_url": str(provider.get("base_url") or ""),
                "key_reference": public_key_reference(first_provider_key(provider)),
                "is_default": str(name) == default_provider,
                "route_count": len(models),
            }
        )
    return {
        "default_provider": default_provider,
        "provider_count": len(items),
        "route_count": len(get_routes(config)),
        "providers": sorted(items, key=lambda item: (not item["is_default"], item["name"])),
    }


def apply_provider(
    config: dict[str, Any],
    *,
    name: str,
    base_url: str,
    models: list[str],
    key_reference: str | None = None,
    make_default: bool = False,
) -> None:
    name = validate_provider_name(name)
    base_url = validate_base_url(base_url)
    selected = sorted({model.strip() for model in models if model.strip()})
    if not selected:
        raise ButterConfigError("Select at least one model")
    providers = config.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise ButterConfigError("providers must be a mapping")
    existing = providers.get(name)
    provider = dict(existing) if isinstance(existing, dict) else {}
    provider["base_url"] = base_url
    # Clean up any legacy or duplicate keys
    provider.pop("Keys", None)
    provider.pop("api_key", None)
    provider.pop("key", None)
    if key_reference is not None:
        provider["keys"] = [{"key": key_reference, "weight": 1}]
    elif not provider.get("keys"):
        provider["keys"] = [
            {
                "key": "ollama" if ":11434" in base_url else "none",
                "weight": 1,
            }
        ]
    providers[name] = provider

    routes = get_routes(config, create=True)
    for model_id, route in list(routes.items()):
        if not isinstance(route, dict):
            continue
        route_providers = normalize_route_providers(route)
        if name not in route_providers:
            continue
        remaining = [item for item in route_providers if item != name]
        if remaining:
            if len(remaining) == 1:
                route["provider"] = remaining[0]
                route.pop("providers", None)
            else:
                route["providers"] = remaining
                route.pop("provider", None)
        else:
            routes.pop(model_id, None)
    for model_id in selected:
        route_id, upstream = normalize_route_and_upstream(name, model_id)
        existing_route = routes.get(route_id) or routes.get(model_id) or {}
        route = dict(existing_route) if isinstance(existing_route, dict) else {}
        route["provider"] = name
        route["model"] = upstream
        if "label" not in route:
            route["label"] = derive_model_label(upstream)
        if "family" not in route:
            route["family"] = derive_model_family(upstream)
        route.setdefault("strategy", "priority")
        routes[route_id] = route
    routing = config.setdefault("routing", {})
    if make_default or not routing.get("default_provider"):
        routing["default_provider"] = name


def delete_provider(config: dict[str, Any], name: str) -> None:
    name = validate_provider_name(name)
    providers = config.get("providers")
    if not isinstance(providers, dict) or name not in providers:
        raise ButterConfigError(f"Provider not found: {name}")
    routing = config.get("routing")
    if isinstance(routing, dict) and routing.get("default_provider") == name:
        raise ButterConfigError("Choose another default provider before deleting this one")
    providers.pop(name)
    routes = get_routes(config)
    for model_id, route in list(routes.items()):
        route_providers = route.get("providers") if isinstance(route, dict) else None
        if not isinstance(route_providers, list) or name not in route_providers:
            continue
        remaining = [item for item in route_providers if item != name]
        if remaining:
            route["providers"] = remaining
        else:
            routes.pop(model_id, None)


def apply_route(
    config: dict[str, Any],
    *,
    provider: str | None = None,
    providers: list[str] | None = None,
    model: str,
    route_id: str | None = None,
    label: str | None = None,
    family: str | None = None,
    strategy: str = "priority",
    extra_body: dict[str, Any] | None = None,
) -> str:
    all_providers = config.get("providers")
    if not isinstance(all_providers, dict):
        raise ButterConfigError("Butter config must contain a providers section")

    target_providers: list[str] = []
    if providers:
        target_providers = [validate_provider_name(p) for p in providers if p]
    elif provider:
        target_providers = [validate_provider_name(provider)]
    else:
        raise ButterConfigError("Route must specify --provider or --providers")

    for p in target_providers:
        if p not in all_providers:
            norm_p = normalize_provider_name(p)
            if norm_p not in all_providers:
                raise ButterConfigError(f"Provider {p!r} is not defined in providers section")

    clean_model = model.strip()
    if not clean_model:
        raise ButterConfigError("Model ID cannot be empty")

    primary_prov = target_providers[0]
    canonical_route, upstream = normalize_route_and_upstream(primary_prov, clean_model)
    clean_route = (route_id or (clean_model if len(target_providers) > 1 else canonical_route)).strip()
    clean_label = (label or derive_model_label(upstream)).strip()
    clean_family = (family or derive_model_family(upstream)).strip()

    routes = get_routes(config, create=True)
    existing_route = routes.get(clean_route, {})
    if not isinstance(existing_route, dict):
        existing_route = {}

    route_entry: dict[str, Any] = {
        "model": upstream,
        "label": clean_label,
        "family": clean_family,
        "strategy": strategy,
    }
    if len(target_providers) > 1:
        route_entry["providers"] = target_providers
    else:
        route_entry["provider"] = target_providers[0]

    # Preserve or assign extra_body
    if extra_body is not None:
        if not isinstance(extra_body, dict):
            raise ButterConfigError("extra_body must be a mapping")
        route_entry["extra_body"] = extra_body
    elif "extra_body" in existing_route:
        route_entry["extra_body"] = existing_route["extra_body"]

    routes[clean_route] = route_entry
    return clean_route


def remove_route(config: dict[str, Any], route_id: str) -> bool:
    routes = get_routes(config, create=False)
    if route_id in routes:
        routes.pop(route_id)
        return True
    return False


def list_routes(config: dict[str, Any]) -> list[dict[str, Any]]:
    routes = get_routes(config, create=False)
    result = []
    for route_id, route in sorted(routes.items()):
        if not isinstance(route, dict):
            continue
        providers = normalize_route_providers(route)
        prov = ", ".join(providers) if len(providers) > 1 else (providers[0] if providers else "")
        primary = providers[0] if providers else ""
        upstream = route.get("model") or route_id.removeprefix(f"{primary}/")
        entry = {
            "route_id": route_id,
            "provider": prov,
            "model": upstream,
            "label": route.get("label") or derive_model_label(upstream),
            "family": route.get("family") or derive_model_family(upstream),
            "strategy": route.get("strategy", "priority"),
        }
        if "extra_body" in route:
            entry["extra_body"] = route["extra_body"]
        result.append(entry)
    return result


def discover_models(base_url: str, api_key: str = "", timeout: float = 30) -> list[str]:
    require_core_dependencies()
    target = validate_base_url(base_url) + "/models"
    headers = {"Accept": "application/json"}
    if api_key and api_key not in {"ollama", "none"}:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.get(target, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ButterConfigError(f"Model discovery failed: {exc}") from exc
    except ValueError as exc:
        raise ButterConfigError("Model endpoint returned invalid JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    models = sorted(
        {
            str(item["id"]).strip()
            for item in data or []
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
    )
    if not models:
        raise ButterConfigError("Model endpoint returned no model IDs")
    return models


def sync_catalogs(
    config: dict[str, Any],
    env: dict[str, str],
    selected_providers: set[str] | None = None,
    prune: bool = True,
    fetcher: Callable[[str, str], list[str]] | None = None,
) -> dict[str, Any]:
    providers = config.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ButterConfigError("Butter config has no providers")
    fetcher = fetcher or (lambda base_url, api_key: discover_models(base_url, api_key))
    discovered: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for name, raw_provider in providers.items():
        if selected_providers and name not in selected_providers:
            continue
        provider = raw_provider if isinstance(raw_provider, dict) else {}
        try:
            base_url = validate_base_url(str(provider.get("base_url") or ""))
            key = resolve_key(first_provider_key(provider), env)
            discovered[str(name)] = fetcher(base_url, key)
        except Exception as exc:
            errors[str(name)] = str(exc)
    if not discovered:
        raise ButterConfigError("No catalogs fetched. " + "; ".join(f"{k}: {v}" for k, v in errors.items()))

    routes = get_routes(config, create=True)
    before = set(routes)
    successful = set(discovered)
    if prune:
        for model_id, route in list(routes.items()):
            if not isinstance(route, dict):
                continue
            route_providers = normalize_route_providers(route)
            if not route_providers:
                continue
            new_providers = []
            for p in route_providers:
                if p in successful:
                    m = route.get("model") or str(model_id).removeprefix(f"{p}/")
                    _, canon_m = normalize_route_and_upstream(p, m)
                    if canon_m in discovered[p] or str(model_id) in discovered[p]:
                        new_providers.append(p)
                else:
                    new_providers.append(p)
            if new_providers:
                if len(new_providers) == 1:
                    route["provider"] = new_providers[0]
                    route.pop("providers", None)
                else:
                    route["providers"] = new_providers
                    route.pop("provider", None)
            else:
                routes.pop(model_id, None)
    for provider_name, model_ids in discovered.items():
        for model_id in model_ids:
            route_id, upstream = normalize_route_and_upstream(provider_name, model_id)
            route = routes.setdefault(route_id, {})
            route["provider"] = provider_name
            route["model"] = upstream
            if "label" not in route:
                route["label"] = derive_model_label(upstream)
            if "family" not in route:
                route["family"] = derive_model_family(upstream)
            route.setdefault("strategy", "priority")
    after = set(routes)
    return {
        "providers_synced": sorted(discovered),
        "provider_counts": {name: len(models) for name, models in discovered.items()},
        "models_discovered": sum(len(models) for models in discovered.values()),
        "routes_added": len(after - before),
        "routes_removed": len(before - after),
        "routes_total": len(after),
        "errors": errors,
    }


class SSHManager:
    """Paramiko transport with atomic SFTP writes and optional sudo operations."""

    def __init__(self) -> None:
        self.client: Any = None
        self.sftp: Any = None
        self.connected = False
        self.sudo_password = ""

    def connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
        auth_method: str,
        password: str = "",
        key_path: str = "",
        key_passphrase: str = "",
        trust_new_host: bool = False,
        sudo_password: str = "",
    ) -> str:
        if paramiko is None:
            raise ButterConfigError("SSH support requires: python -m pip install paramiko")
        if not host.strip() or not username.strip():
            raise ButterConfigError("SSH host and username are required")
        if not 1 <= int(port) <= 65535:
            raise ButterConfigError("SSH port must be between 1 and 65535")
        self.disconnect()
        client = paramiko.SSHClient()
        known_hosts = Path.home() / ".ssh" / "known_hosts"
        client.load_system_host_keys()
        if known_hosts.exists():
            client.load_host_keys(str(known_hosts))
        if trust_new_host:
            known_hosts.parent.mkdir(parents=True, exist_ok=True)
            known_hosts.touch(exist_ok=True)
            try:
                known_hosts.chmod(0o600)
            except OSError:
                pass
            client.load_host_keys(str(known_hosts))
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

        options: dict[str, Any] = {
            "hostname": host.strip(),
            "port": int(port),
            "username": username.strip(),
            "timeout": 15,
            "banner_timeout": 15,
            "auth_timeout": 15,
        }
        if auth_method == "key":
            key_file = Path(key_path).expanduser()
            if not key_file.is_file():
                raise ButterConfigError(f"Private key not found: {key_file}")
            options.update(
                key_filename=str(key_file),
                passphrase=key_passphrase or None,
                allow_agent=False,
                look_for_keys=False,
            )
        elif auth_method == "agent":
            options.update(allow_agent=True, look_for_keys=True)
        else:
            if not password:
                raise ButterConfigError("SSH password is required")
            options.update(password=password, allow_agent=False, look_for_keys=False)
        try:
            client.connect(**options)
            self.client = client
            self.sftp = client.open_sftp()
            self.connected = True
            self.sudo_password = sudo_password or password
            key = client.get_transport().get_remote_server_key()
            return key.get_fingerprint().hex(":")
        except Exception as exc:
            client.close()
            raise ButterConfigError(f"SSH connection failed: {exc}") from exc

    def disconnect(self) -> None:
        if self.sftp is not None:
            try:
                self.sftp.close()
            except Exception:
                pass
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        self.sftp = None
        self.connected = False
        self.sudo_password = ""

    def _require(self) -> None:
        if not self.connected or self.client is None:
            raise ButterConfigError("Connect over SSH first")

    def execute(
        self,
        command: str,
        *,
        use_sudo: bool = False,
        stdin_text: str = "",
        timeout: int = 60,
    ) -> tuple[str, str]:
        self._require()
        actual = command
        input_text = stdin_text
        if use_sudo:
            actual = "sudo -S -p '' sh -c " + shlex.quote(command)
            input_text = self.sudo_password + "\n" + stdin_text
        stdin, stdout, stderr = self.client.exec_command(actual, timeout=timeout)
        if input_text:
            stdin.write(input_text)
        stdin.channel.shutdown_write()
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if code != 0:
            raise ButterConfigError(err.strip() or f"Remote command failed with exit code {code}")
        return out, err

    def read_file(self, remote_path: str, *, use_sudo: bool = False) -> str:
        self._require()
        remote_path = validate_remote_path(remote_path)
        if use_sudo:
            out, _ = self.execute("cat -- " + shlex.quote(remote_path), use_sudo=True)
            return out
        try:
            with self.sftp.file(remote_path, "r") as handle:
                data = handle.read()
            return data.decode("utf-8") if isinstance(data, bytes) else str(data)
        except Exception as exc:
            raise ButterConfigError(f"Cannot read {remote_path}: {exc}") from exc

    def write_file(
        self,
        remote_path: str,
        content: str,
        *,
        use_sudo: bool = False,
        backup: bool = True,
    ) -> str | None:
        self._require()
        remote_path = validate_remote_path(remote_path)
        backup_path = remote_path + ".bak" if backup else None
        if use_sudo:
            temporary = f"/tmp/.butter-manager-{uuid.uuid4().hex}"
            try:
                with self.sftp.file(temporary, "w") as handle:
                    handle.write(content)
                script = ["set -eu", f"target={shlex.quote(remote_path)}"]
                if backup:
                    script.append('[ ! -e "$target" ] || cp -p -- "$target" "$target.bak"')
                script.extend(
                    [
                        'mkdir -p -- "$(dirname -- "$target")"',
                        f"install -m 0600 -- {shlex.quote(temporary)} \"$target\"",
                    ]
                )
                self.execute("; ".join(script), use_sudo=True)
            finally:
                try:
                    self.sftp.remove(temporary)
                except Exception:
                    pass
            return backup_path

        directory = str(PurePosixPath(remote_path).parent)
        temporary = f"{directory}/.butter-manager-{uuid.uuid4().hex}"
        try:
            original_mode = 0o600
            try:
                original_mode = stat.S_IMODE(self.sftp.stat(remote_path).st_mode)
                if backup:
                    with self.sftp.file(remote_path, "rb") as source:
                        old_data = source.read()
                    with self.sftp.file(backup_path, "wb") as target:
                        target.write(old_data)
                    self.sftp.chmod(backup_path, original_mode)
            except OSError:
                backup_path = None
            with self.sftp.file(temporary, "w") as handle:
                handle.write(content)
            self.sftp.chmod(temporary, original_mode)
            try:
                self.sftp.posix_rename(temporary, remote_path)
            except Exception:
                try:
                    self.sftp.remove(remote_path)
                except OSError:
                    pass
                self.sftp.rename(temporary, remote_path)
            return backup_path
        except Exception as exc:
            try:
                self.sftp.remove(temporary)
            except Exception:
                pass
            raise ButterConfigError(f"Cannot write {remote_path}: {exc}") from exc

    def list_directory(self, remote_path: str, *, use_sudo: bool = False) -> list[dict[str, Any]]:
        self._require()
        remote_path = validate_remote_path(remote_path)
        if use_sudo:
            command = (
                "find "
                + shlex.quote(remote_path)
                + " -mindepth 1 -maxdepth 1 -printf '%f\\t%y\\t%s\\n'"
            )
            out, _ = self.execute(command, use_sudo=True)
            entries = []
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    entries.append(
                        {
                            "name": parts[0],
                            "is_dir": parts[1] == "d",
                            "size": int(parts[2] or 0),
                        }
                    )
        else:
            try:
                entries = [
                    {
                        "name": item.filename,
                        "is_dir": stat.S_ISDIR(item.st_mode),
                        "size": item.st_size,
                    }
                    for item in self.sftp.listdir_attr(remote_path)
                ]
            except Exception as exc:
                raise ButterConfigError(f"Cannot list {remote_path}: {exc}") from exc
        return sorted(entries, key=lambda item: (not item["is_dir"], item["name"].lower()))

    def discover_models(self, base_url: str, api_key: str = "") -> list[str]:
        """Run discovery on remote host so 127.0.0.1 means remote server."""
        self._require()
        payload = json.dumps({"base_url": validate_base_url(base_url), "api_key": api_key})
        encoded = __import__("base64").b64encode(payload.encode()).decode()
        script = f"""
import base64, json, urllib.request
p = json.loads(base64.b64decode({encoded!r}))
h = {{'Accept': 'application/json'}}
if p['api_key'] and p['api_key'] not in ('ollama', 'none'):
    h['Authorization'] = 'Bearer ' + p['api_key']
r = urllib.request.Request(p['base_url'].rstrip('/') + '/models', headers=h)
with urllib.request.urlopen(r, timeout=30) as response:
    data = json.load(response)
print(json.dumps(sorted({{str(x['id']).strip() for x in data.get('data', []) if isinstance(x, dict) and str(x.get('id', '')).strip()}})))
"""
        out, _ = self.execute("python3 -", stdin_text=script, timeout=45)
        try:
            models = json.loads(out)
        except ValueError as exc:
            raise ButterConfigError("Remote model discovery returned invalid JSON") from exc
        if not isinstance(models, list) or not models:
            raise ButterConfigError("Remote model endpoint returned no model IDs")
        return [str(model) for model in models]


def validate_remote_path(value: str) -> str:
    clean = value.strip()
    if not clean.startswith("/") or any(character in clean for character in "\x00\r\n"):
        raise ButterConfigError("Remote path must be an absolute Linux path")
    return str(PurePosixPath(clean))


def read_optional_remote(ssh: SSHManager, path: str, use_sudo: bool) -> str:
    try:
        return ssh.read_file(path, use_sudo=use_sudo)
    except ButterConfigError:
        return ""


def import_tkinter() -> tuple[Any, Any, Any, Any, Any]:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
    except ImportError as exc:
        raise ButterConfigError("GUI requires Tkinter (often packaged as python3-tk)") from exc
    return tk, ttk, scrolledtext, messagebox, filedialog


class ButterConfigGUI:
    def __init__(self, root: Any, args: argparse.Namespace) -> None:
        tk, ttk, scrolledtext, messagebox, filedialog = import_tkinter()
        self.tk = tk
        self.ttk = ttk
        self.scrolledtext = scrolledtext
        self.messagebox = messagebox
        self.filedialog = filedialog
        self.root = root
        self.args = args
        self.root.title(f"{APP_NAME} {VERSION}")
        self.root.geometry("1040x860")
        self.root.minsize(880, 700)
        self.config: dict[str, Any] = {"providers": {}, "routing": {"models": {}}}
        self.config_path = Path(args.config or "config.yaml")
        self.env_path = Path(args.env or ".env")
        self.fetched_models: list[str] = []
        self.chosen_models: set[str] = set()
        self.pending_env: dict[str, str] = {}
        self.ssh = SSHManager()
        self.saved_endpoints: dict[str, dict[str, str]] = load_json(ENDPOINTS_PATH, {})
        self.saved_profiles: dict[str, dict[str, Any]] = load_json(SSH_PROFILES_PATH, {})
        self._build_ui()
        self._apply_args()
        if self.config_path.is_file():
            self._load_local_path(self.config_path, quiet=True)

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        main = ttk.Frame(notebook, padding=10)
        remote = ttk.Frame(notebook, padding=10)
        presets = ttk.Frame(notebook, padding=10)
        notebook.add(main, text="Config & Models")
        notebook.add(remote, text="SSH Remote")
        notebook.add(presets, text="Endpoint Presets")
        self._build_main_tab(main)
        self._build_remote_tab(remote)
        self._build_presets_tab(presets)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(
            fill=tk.X, side=tk.BOTTOM
        )

    def _build_main_tab(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        endpoint = ttk.LabelFrame(parent, text="Provider", padding=10)
        endpoint.pack(fill=tk.X, pady=(0, 8))
        self.provider_var = tk.StringVar(value="openrouter")
        self.base_url_var = tk.StringVar(value="https://openrouter.ai/api/v1")
        self.api_key_var = tk.StringVar()
        self.key_env_var = tk.StringVar(value="OPENROUTER_API_KEY")
        self.default_var = tk.BooleanVar(value=True)
        self.no_auth_var = tk.BooleanVar(value=False)
        self.remote_discovery_var = tk.BooleanVar(value=False)
        ttk.Label(endpoint, text="Provider name").grid(row=0, column=0, sticky=tk.W)
        self.provider_combo = ttk.Combobox(endpoint, textvariable=self.provider_var, width=25)
        self.provider_combo.grid(row=0, column=1, sticky=tk.EW, padx=5)
        self.provider_combo.bind("<<ComboboxSelected>>", self._provider_selected)
        ttk.Label(endpoint, text="Base URL").grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        ttk.Entry(endpoint, textvariable=self.base_url_var, width=48).grid(
            row=0, column=3, sticky=tk.EW, padx=5
        )
        ttk.Label(endpoint, text="API key (blank preserves existing)").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(endpoint, textvariable=self.api_key_var, show="*", width=28).grid(
            row=1, column=1, sticky=tk.EW, padx=5
        )
        ttk.Label(endpoint, text="Environment variable").grid(row=1, column=2, sticky=tk.W, padx=(12, 0))
        ttk.Entry(endpoint, textvariable=self.key_env_var).grid(row=1, column=3, sticky=tk.EW, padx=5)
        checks = ttk.Frame(endpoint)
        checks.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(5, 0))
        ttk.Checkbutton(checks, text="Default provider", variable=self.default_var).pack(side=tk.LEFT)
        ttk.Checkbutton(checks, text="No authentication", variable=self.no_auth_var).pack(side=tk.LEFT, padx=12)
        ttk.Checkbutton(
            checks,
            text="Run discovery on connected SSH host",
            variable=self.remote_discovery_var,
        ).pack(side=tk.LEFT)
        self.fetch_button = ttk.Button(checks, text="Fetch Models", command=self.fetch_models)
        self.fetch_button.pack(side=tk.LEFT, padx=12)
        ttk.Button(checks, text="Save Endpoint Preset", command=self.save_endpoint_preset).pack(side=tk.LEFT)
        endpoint.columnconfigure(1, weight=1)
        endpoint.columnconfigure(3, weight=2)

        models = ttk.LabelFrame(parent, text="Models", padding=10)
        models.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        search_row = ttk.Frame(models)
        search_row.pack(fill=tk.X, pady=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_model_tree())
        ttk.Label(search_row, text="Filter").pack(side=tk.LEFT)
        ttk.Entry(search_row, textvariable=self.search_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(search_row, text="Select Visible", command=self.select_visible).pack(side=tk.LEFT)
        ttk.Button(search_row, text="Clear Visible", command=self.clear_visible).pack(side=tk.LEFT, padx=4)
        ttk.Button(search_row, text="Invert Visible", command=self.invert_visible).pack(side=tk.LEFT)
        self.model_tree = ttk.Treeview(models, columns=("use", "model"), show="headings", selectmode="extended")
        self.model_tree.heading("use", text="Use")
        self.model_tree.heading("model", text="Model ID")
        self.model_tree.column("use", width=55, stretch=False, anchor=tk.CENTER)
        self.model_tree.column("model", width=720)
        self.model_tree.bind("<Double-1>", self.toggle_tree_model)
        scrollbar = ttk.Scrollbar(models, orient=tk.VERTICAL, command=self.model_tree.yview)
        self.model_tree.configure(yscrollcommand=scrollbar.set)
        self.model_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        actions = ttk.Frame(parent)
        actions.pack(fill=tk.X, pady=(0, 8))
        self.model_count_var = tk.StringVar(value="0 models loaded; 0 selected")
        ttk.Label(actions, textvariable=self.model_count_var).pack(side=tk.LEFT)
        ttk.Button(actions, text="Apply Provider & Routes", command=self.apply_provider_changes).pack(
            side=tk.RIGHT
        )
        ttk.Button(actions, text="Delete Provider", command=self.delete_current_provider).pack(
            side=tk.RIGHT, padx=5
        )
        ttk.Button(actions, text="Sync All Catalogs", command=self.sync_all_catalogs).pack(
            side=tk.RIGHT
        )

        preview = ttk.LabelFrame(parent, text="Butter YAML (editable)", padding=8)
        preview.pack(fill=tk.BOTH, expand=True)
        preview_actions = ttk.Frame(preview)
        preview_actions.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(preview_actions, text="Load Local", command=self.load_local_config).pack(side=tk.LEFT)
        ttk.Button(preview_actions, text="Validate", command=self.validate_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(preview_actions, text="Save Local", command=self.save_local_config).pack(side=tk.LEFT)
        self.preview_text = self.scrolledtext.ScrolledText(preview, height=12, font=("Consolas", 9), undo=True)
        self.preview_text.pack(fill=tk.BOTH, expand=True)
        self._set_config(self.config)

    def _build_remote_tab(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        self.profile_var = tk.StringVar()
        self.host_var = tk.StringVar()
        self.port_var = tk.IntVar(value=22)
        self.user_var = tk.StringVar()
        self.auth_var = tk.StringVar(value="agent")
        self.password_var = tk.StringVar()
        self.key_path_var = tk.StringVar(value="~/.ssh/id_ed25519")
        self.key_passphrase_var = tk.StringVar()
        self.trust_host_var = tk.BooleanVar(value=False)
        self.use_sudo_var = tk.BooleanVar(value=False)
        self.sudo_password_var = tk.StringVar()
        self.remote_config_var = tk.StringVar(value=str(DEFAULT_CONFIG_PATH))
        self.remote_env_var = tk.StringVar(value=str(DEFAULT_ENV_PATH))
        self.service_var = tk.StringVar(value="butter")
        self.restart_after_save_var = tk.BooleanVar(value=False)

        profile = ttk.LabelFrame(parent, text="Saved profile (secrets never saved)", padding=10)
        profile.pack(fill=tk.X, pady=(0, 8))
        self.profile_combo = ttk.Combobox(profile, textvariable=self.profile_var, state="readonly")
        self.profile_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.profile_combo.bind("<<ComboboxSelected>>", self.load_ssh_profile)
        ttk.Button(profile, text="Save Profile", command=self.save_ssh_profile).pack(side=tk.LEFT, padx=5)
        ttk.Button(profile, text="Delete Profile", command=self.delete_ssh_profile).pack(side=tk.LEFT)

        connection = ttk.LabelFrame(parent, text="SSH/SFTP connection", padding=10)
        connection.pack(fill=tk.X, pady=(0, 8))
        fields = [
            ("Host/IP", self.host_var, 0, 0),
            ("Port", self.port_var, 0, 2),
            ("Username", self.user_var, 1, 0),
            ("Auth", self.auth_var, 1, 2),
        ]
        for label, variable, row, column in fields:
            ttk.Label(connection, text=label).grid(row=row, column=column, sticky=tk.W, pady=3)
            if label == "Auth":
                widget = ttk.Combobox(
                    connection,
                    textvariable=variable,
                    values=("agent", "key", "password"),
                    state="readonly",
                    width=16,
                )
            else:
                widget = ttk.Entry(connection, textvariable=variable, width=30)
            widget.grid(row=row, column=column + 1, sticky=tk.EW, padx=5)
        ttk.Label(connection, text="Password").grid(row=2, column=0, sticky=tk.W, pady=3)
        ttk.Entry(connection, textvariable=self.password_var, show="*").grid(row=2, column=1, sticky=tk.EW, padx=5)
        ttk.Label(connection, text="Private key").grid(row=2, column=2, sticky=tk.W, pady=3)
        key_row = ttk.Frame(connection)
        key_row.grid(row=2, column=3, sticky=tk.EW, padx=5)
        ttk.Entry(key_row, textvariable=self.key_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(key_row, text="Browse", command=self.browse_private_key).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Label(connection, text="Key passphrase").grid(row=3, column=0, sticky=tk.W, pady=3)
        ttk.Entry(connection, textvariable=self.key_passphrase_var, show="*").grid(row=3, column=1, sticky=tk.EW, padx=5)
        ttk.Label(connection, text="Sudo password").grid(row=3, column=2, sticky=tk.W, pady=3)
        ttk.Entry(connection, textvariable=self.sudo_password_var, show="*").grid(row=3, column=3, sticky=tk.EW, padx=5)
        checks = ttk.Frame(connection)
        checks.grid(row=4, column=0, columnspan=4, sticky=tk.W, pady=5)
        ttk.Checkbutton(checks, text="Trust and save new host key", variable=self.trust_host_var).pack(side=tk.LEFT)
        ttk.Checkbutton(checks, text="Use sudo for files", variable=self.use_sudo_var).pack(side=tk.LEFT, padx=12)
        self.connect_button = ttk.Button(checks, text="Connect", command=self.connect_ssh)
        self.connect_button.pack(side=tk.LEFT)
        self.disconnect_button = ttk.Button(checks, text="Disconnect", command=self.disconnect_ssh, state=tk.DISABLED)
        self.disconnect_button.pack(side=tk.LEFT, padx=5)
        connection.columnconfigure(1, weight=1)
        connection.columnconfigure(3, weight=1)

        paths = ttk.LabelFrame(parent, text="Remote Butter paths", padding=10)
        paths.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(paths, text="Config YAML").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(paths, textvariable=self.remote_config_var).grid(row=0, column=1, sticky=tk.EW, padx=5)
        ttk.Button(paths, text="Browse", command=self.browse_remote).grid(row=0, column=2)
        ttk.Label(paths, text="Environment file").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(paths, textvariable=self.remote_env_var).grid(row=1, column=1, sticky=tk.EW, padx=5)
        ttk.Label(paths, text="Systemd service").grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(paths, textvariable=self.service_var).grid(row=2, column=1, sticky=tk.EW, padx=5)
        ttk.Checkbutton(paths, text="Restart after save", variable=self.restart_after_save_var).grid(row=2, column=2)
        paths.columnconfigure(1, weight=1)

        actions = ttk.LabelFrame(parent, text="Remote actions", padding=10)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="Load Remote Config", command=self.load_remote_config).pack(side=tk.LEFT)
        ttk.Button(actions, text="Save Remote Config", command=self.save_remote_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions, text="Restart Service", command=self.restart_remote_service).pack(side=tk.LEFT)
        self.remote_status_var = tk.StringVar(value="Not connected")
        ttk.Label(actions, textvariable=self.remote_status_var).pack(side=tk.LEFT, padx=15)
        self._refresh_profiles()

    def _build_presets_tab(self, parent: Any) -> None:
        tk, ttk = self.tk, self.ttk
        built_in = ttk.LabelFrame(parent, text="Built-in endpoints", padding=10)
        built_in.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.builtin_tree = ttk.Treeview(built_in, columns=("name", "url"), show="headings")
        self.builtin_tree.heading("name", text="Name")
        self.builtin_tree.heading("url", text="URL")
        self.builtin_tree.pack(fill=tk.BOTH, expand=True)
        for item in ENDPOINT_PRESETS:
            self.builtin_tree.insert("", tk.END, values=(item[0], item[1]))
        self.builtin_tree.bind("<Double-1>", self.load_builtin_preset)
        saved = ttk.LabelFrame(parent, text="Saved endpoints (API keys excluded)", padding=10)
        saved.pack(fill=tk.BOTH, expand=True)
        self.saved_tree = ttk.Treeview(saved, columns=("name", "url"), show="headings")
        self.saved_tree.heading("name", text="Name")
        self.saved_tree.heading("url", text="URL")
        self.saved_tree.pack(fill=tk.BOTH, expand=True)
        row = ttk.Frame(saved)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(row, text="Load Selected", command=self.load_saved_preset).pack(side=tk.LEFT)
        ttk.Button(row, text="Delete Selected", command=self.delete_saved_preset).pack(side=tk.LEFT, padx=5)
        self._refresh_endpoint_presets()

    def _apply_args(self) -> None:
        if self.args.host:
            self.host_var.set(self.args.host)
        if self.args.port:
            self.port_var.set(self.args.port)
        if self.args.user:
            self.user_var.set(self.args.user)
        if self.args.key:
            self.key_path_var.set(self.args.key)
            self.auth_var.set("key")
        if self.args.remote_config:
            self.remote_config_var.set(self.args.remote_config)
        if self.args.remote_env:
            self.remote_env_var.set(self.args.remote_env)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _show_error(self, exc: Exception | str) -> None:
        text = str(exc)
        self._set_status(f"Error: {text}")
        self.messagebox.showerror(APP_NAME, text, parent=self.root)

    def _background(self, label: str, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        self._set_status(label)
        self.fetch_button.configure(state=self.tk.DISABLED)

        def runner() -> None:
            try:
                result = work()
                self.root.after(0, lambda: done(result))
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._show_error(error))
            finally:
                self.root.after(0, lambda: self.fetch_button.configure(state=self.tk.NORMAL))

        threading.Thread(target=runner, daemon=True).start()

    def _set_config(self, config: dict[str, Any]) -> None:
        self.config = config
        self.preview_text.delete("1.0", self.tk.END)
        self.preview_text.insert("1.0", dump_config(config))
        self._refresh_provider_values()

    def _parse_preview(self, strict: bool = True) -> dict[str, Any]:
        config = load_config_text(self.preview_text.get("1.0", self.tk.END))
        validate_butter_config(config, strict=strict)
        self.config = config
        return config

    def _refresh_provider_values(self) -> None:
        configured = list((self.config.get("providers") or {}).keys())
        values = sorted(
            set(configured)
            | set(self.saved_endpoints)
            | {prov_type for _, _, prov_type, _ in ENDPOINT_PRESETS}
            | {name for name, _, _, _ in ENDPOINT_PRESETS}
        )
        self.provider_combo.configure(values=values)

    def _provider_selected(self, _event: Any = None) -> None:
        name = self.provider_var.get().strip()
        providers = self.config.get("providers")
        provider = providers.get(name) if isinstance(providers, dict) else None
        if isinstance(provider, dict):
            self.base_url_var.set(str(provider.get("base_url") or ""))
            reference = first_provider_key(provider)
            var_name = extract_env_name(reference)
            self.key_env_var.set(var_name if var_name else "")
            self.no_auth_var.set(reference in {"none", "ollama"})
            routing = self.config.get("routing")
            self.default_var.set(isinstance(routing, dict) and routing.get("default_provider") == name)
            self.chosen_models = set(provider_models(self.config, name))
            self.fetched_models = sorted(self.chosen_models)
        elif name in self.saved_endpoints:
            self.base_url_var.set(self.saved_endpoints[name]["url"])
            self.default_var.set(False)
            self.chosen_models = set()
            self.fetched_models = []
        else:
            self.default_var.set(False)
            self.chosen_models = set()
            self.fetched_models = []
            preset = next((p for p in ENDPOINT_PRESETS if p[0].lower() == name.lower() or p[2].lower() == name.lower()), None)
            if preset:
                _, url, prov_type, key_env = preset
                self.provider_var.set(prov_type)
                self.base_url_var.set(url)
                if key_env == "none":
                    self.no_auth_var.set(True)
                    self.key_env_var.set("")
                else:
                    self.no_auth_var.set(False)
                    clean_preset_env = extract_env_name(key_env)
                    self.key_env_var.set(clean_preset_env or key_env)
                configured_prov = (self.config.get("providers") or {}).get(prov_type)
                if isinstance(configured_prov, dict):
                    self.chosen_models = set(provider_models(self.config, prov_type))
                    self.fetched_models = sorted(self.chosen_models)
        self.api_key_var.set("")
        self._refresh_model_tree()

    def _visible_models(self) -> list[str]:
        query = self.search_var.get().strip().lower()
        return [model for model in self.fetched_models if not query or query in model.lower()]

    def _refresh_model_tree(self) -> None:
        if not hasattr(self, "model_tree"):
            return
        self.model_tree.delete(*self.model_tree.get_children())
        for model in self._visible_models():
            self.model_tree.insert("", self.tk.END, values=("✓" if model in self.chosen_models else "", model))
        self.model_count_var.set(
            f"{len(self.fetched_models)} models loaded; {len(self.chosen_models)} selected"
        )

    def select_visible(self) -> None:
        self.chosen_models.update(self._visible_models())
        self._refresh_model_tree()

    def clear_visible(self) -> None:
        self.chosen_models.difference_update(self._visible_models())
        self._refresh_model_tree()

    def invert_visible(self) -> None:
        for model in self._visible_models():
            if model in self.chosen_models:
                self.chosen_models.remove(model)
            else:
                self.chosen_models.add(model)
        self._refresh_model_tree()

    def toggle_tree_model(self, _event: Any = None) -> None:
        for item in self.model_tree.selection():
            model = str(self.model_tree.item(item)["values"][1])
            if model in self.chosen_models:
                self.chosen_models.remove(model)
            else:
                self.chosen_models.add(model)
        self._refresh_model_tree()

    def _current_key(self, remote: bool = False) -> str:
        supplied = self.api_key_var.get().strip()
        if supplied:
            return supplied
        providers = self.config.get("providers")
        provider = providers.get(self.provider_var.get().strip()) if isinstance(providers, dict) else None
        reference = first_provider_key(provider) if isinstance(provider, dict) else ""
        if not reference and not self.no_auth_var.get():
            env_var = extract_env_name(self.key_env_var.get())
            if env_var:
                reference = f"${{{env_var}}}"
        if remote and self.ssh.connected:
            text = read_optional_remote(
                self.ssh, self.remote_env_var.get(), self.use_sudo_var.get()
            )
            return resolve_key(reference, load_env_text(text))
        return resolve_key(reference, load_env_file(self.env_path))

    def fetch_models(self) -> None:
        base_url = self.base_url_var.get()
        remote = self.remote_discovery_var.get()

        def work() -> list[str]:
            key = self._current_key(remote=remote)
            return self.ssh.discover_models(base_url, key) if remote else discover_models(base_url, key)

        def done(models: list[str]) -> None:
            self.fetched_models = models
            current_provider = self.provider_var.get().strip()
            existing_for_prov = set(provider_models(self.config, current_provider))
            self.chosen_models = (self.chosen_models | existing_for_prov) & set(models)
            if not self.chosen_models and existing_for_prov:
                self.chosen_models = existing_for_prov
            self._refresh_model_tree()
            self._set_status(f"Fetched {len(models)} models")

        self._background("Fetching model catalog…", work, done)

    def apply_provider_changes(self) -> None:
        try:
            config = self._parse_preview(strict=False)
            key_reference: str | None = None
            api_key = self.api_key_var.get().strip()
            env_var_input = self.key_env_var.get().strip()
            if self.no_auth_var.get():
                key_reference = "none"
            elif env_var_input:
                clean_env_name = extract_env_name(env_var_input)
                if not clean_env_name:
                    raise ButterConfigError(
                        f"Invalid environment variable name {env_var_input!r}. "
                        "Expected letters, numbers, and underscores (e.g. GROQ_API_KEY)"
                    )
                key_reference = f"${{{clean_env_name}}}"
                if api_key:
                    self.pending_env[clean_env_name] = api_key
            elif api_key:
                default_env = f"{self.provider_var.get().strip().upper().replace('-', '_')}_API_KEY"
                clean_default = extract_env_name(default_env) or "API_KEY"
                self.pending_env[clean_default] = api_key
                key_reference = f"${{{clean_default}}}"
            apply_provider(
                config,
                name=self.provider_var.get(),
                base_url=self.base_url_var.get(),
                models=sorted(self.chosen_models),
                key_reference=key_reference,
                make_default=self.default_var.get(),
            )
            self.api_key_var.set("")
            self._set_config(config)
            self._set_status(f"Applied provider {self.provider_var.get()} to preview")
        except Exception as exc:
            self._show_error(exc)

    def delete_current_provider(self) -> None:
        name = self.provider_var.get().strip()
        if not self.messagebox.askyesno(APP_NAME, f"Delete provider {name!r} and its routes?", parent=self.root):
            return
        try:
            config = self._parse_preview(strict=False)
            delete_provider(config, name)
            self.chosen_models.clear()
            self.fetched_models.clear()
            self._set_config(config)
            self._set_status(f"Deleted provider {name} from preview")
        except Exception as exc:
            self._show_error(exc)

    def sync_all_catalogs(self) -> None:
        remote = self.remote_discovery_var.get()

        def work() -> tuple[dict[str, Any], dict[str, Any]]:
            config = self._parse_preview(strict=False)
            if remote:
                env_text = read_optional_remote(
                    self.ssh, self.remote_env_var.get(), self.use_sudo_var.get()
                )
                env = load_env_text(env_text)
                fetcher = lambda url, key: self.ssh.discover_models(url, key)
            else:
                env = load_env_file(self.env_path)
                fetcher = None
            result = sync_catalogs(config, env, fetcher=fetcher)
            return config, result

        def done(value: tuple[dict[str, Any], dict[str, Any]]) -> None:
            config, result = value
            self._set_config(config)
            self._set_status(
                f"Synced {result['models_discovered']} models; {result['routes_total']} routes"
            )

        self._background("Syncing all configured catalogs…", work, done)

    def validate_preview(self) -> None:
        try:
            config = self._parse_preview(strict=True)
            summary = config_summary(config)
            self._set_status(
                f"Valid Butter config: {summary['provider_count']} providers, {summary['route_count']} routes"
            )
        except Exception as exc:
            self._show_error(exc)

    def _load_local_path(self, path: Path, quiet: bool = False) -> None:
        try:
            config = load_config_file(path)
            self.config_path = path
            self._set_config(config)
            if not quiet:
                self._set_status(f"Loaded {path}")
        except Exception as exc:
            if not quiet:
                self._show_error(exc)

    def load_local_config(self) -> None:
        value = self.filedialog.askopenfilename(
            title="Open Butter config",
            filetypes=[("YAML", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if value:
            self._load_local_path(Path(value))

    def _apply_pending_env_local(self) -> None:
        if not self.pending_env:
            return
        try:
            text = self.env_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for key, value in self.pending_env.items():
            text = update_env_text(text, key, value)
        atomic_write_text(self.env_path, text, backup=True)
        self.pending_env.clear()

    def save_local_config(self) -> None:
        try:
            config = self._parse_preview(strict=True)
            value = self.filedialog.asksaveasfilename(
                title="Save Butter config",
                defaultextension=".yaml",
                initialfile=self.config_path.name,
                filetypes=[("YAML", "*.yaml *.yml"), ("All files", "*.*")],
            )
            if not value:
                return
            self.config_path = Path(value)
            atomic_write_text(self.config_path, dump_config(config), backup=True)
            self._apply_pending_env_local()
            self._set_status(f"Saved {self.config_path}; backup created when replacing")
        except Exception as exc:
            self._show_error(exc)

    def connect_ssh(self) -> None:
        try:
            fingerprint = self.ssh.connect(
                host=self.host_var.get(),
                port=self.port_var.get(),
                username=self.user_var.get(),
                auth_method=self.auth_var.get(),
                password=self.password_var.get(),
                key_path=self.key_path_var.get(),
                key_passphrase=self.key_passphrase_var.get(),
                trust_new_host=self.trust_host_var.get(),
                sudo_password=self.sudo_password_var.get(),
            )
            self.connect_button.configure(state=self.tk.DISABLED)
            self.disconnect_button.configure(state=self.tk.NORMAL)
            self.remote_status_var.set(f"Connected; host key {fingerprint}")
            self._set_status(f"Connected to {self.user_var.get()}@{self.host_var.get()}")
        except Exception as exc:
            self._show_error(exc)

    def disconnect_ssh(self) -> None:
        self.ssh.disconnect()
        self.connect_button.configure(state=self.tk.NORMAL)
        self.disconnect_button.configure(state=self.tk.DISABLED)
        self.remote_status_var.set("Not connected")

    def load_remote_config(self) -> None:
        try:
            text = self.ssh.read_file(
                self.remote_config_var.get(), use_sudo=self.use_sudo_var.get()
            )
            config = load_config_text(text)
            validate_butter_config(config, strict=False)
            self._set_config(config)
            self._set_status(f"Loaded remote {self.remote_config_var.get()}")
        except Exception as exc:
            self._show_error(exc)

    def _apply_pending_env_remote(self) -> None:
        if not self.pending_env:
            return
        path = self.remote_env_var.get()
        text = read_optional_remote(self.ssh, path, self.use_sudo_var.get())
        for key, value in self.pending_env.items():
            text = update_env_text(text, key, value)
        self.ssh.write_file(path, text, use_sudo=self.use_sudo_var.get(), backup=True)
        self.pending_env.clear()

    def save_remote_config(self) -> None:
        try:
            config = self._parse_preview(strict=True)
            self._apply_pending_env_remote()
            backup = self.ssh.write_file(
                self.remote_config_var.get(),
                dump_config(config),
                use_sudo=self.use_sudo_var.get(),
                backup=True,
            )
            if self.restart_after_save_var.get():
                self.restart_remote_service(quiet=True)
            self._set_status(
                f"Saved remote config; backup: {backup or 'new file'}"
                + ("; service restarted" if self.restart_after_save_var.get() else "")
            )
        except Exception as exc:
            self._show_error(exc)

    def restart_remote_service(self, quiet: bool = False) -> None:
        try:
            service = self.service_var.get().strip()
            if not SERVICE_NAME.fullmatch(service):
                raise ButterConfigError("Invalid systemd service name")
            self.ssh.execute(
                "systemctl restart -- " + shlex.quote(service), use_sudo=True, timeout=90
            )
            if not quiet:
                self._set_status(f"Restarted {service}")
        except Exception as exc:
            if quiet:
                raise
            self._show_error(exc)

    def browse_private_key(self) -> None:
        value = self.filedialog.askopenfilename(title="Select SSH private key")
        if value:
            self.key_path_var.set(value)

    def browse_remote(self) -> None:
        try:
            start = str(PurePosixPath(self.remote_config_var.get()).parent)
            dialog = RemoteBrowserDialog(
                self.root, self.ssh, start, self.use_sudo_var.get(), self.tk, self.ttk, self.messagebox
            )
            self.root.wait_window(dialog.window)
            if dialog.selected_path:
                self.remote_config_var.set(dialog.selected_path)
        except Exception as exc:
            self._show_error(exc)

    def save_endpoint_preset(self) -> None:
        try:
            name = validate_provider_name(self.provider_var.get())
            url = validate_base_url(self.base_url_var.get())
            self.saved_endpoints[name] = {"url": url}
            save_json(ENDPOINTS_PATH, self.saved_endpoints)
            self._refresh_endpoint_presets()
            self._refresh_provider_values()
            self._set_status(f"Saved endpoint preset {name}; API key excluded")
        except Exception as exc:
            self._show_error(exc)

    def _refresh_endpoint_presets(self) -> None:
        if not hasattr(self, "saved_tree"):
            return
        self.saved_tree.delete(*self.saved_tree.get_children())
        for name, data in sorted(self.saved_endpoints.items()):
            self.saved_tree.insert("", self.tk.END, values=(name, data.get("url", "")))

    def load_builtin_preset(self, _event: Any = None) -> None:
        selected = self.builtin_tree.selection()
        if selected:
            name, url = self.builtin_tree.item(selected[0])["values"]
            preset = next((p for p in ENDPOINT_PRESETS if p[0] == name), None)
            if preset:
                _, url, prov_type, key_env = preset
                self.provider_var.set(prov_type)
            else:
                self.provider_var.set(str(name).lower().replace(" ", "-"))
            self._provider_selected()

    def load_saved_preset(self) -> None:
        selected = self.saved_tree.selection()
        if selected:
            name, url = self.saved_tree.item(selected[0])["values"]
            self.provider_var.set(name)
            self._provider_selected()

    def delete_saved_preset(self) -> None:
        selected = self.saved_tree.selection()
        if not selected:
            return
        name = str(self.saved_tree.item(selected[0])["values"][0])
        self.saved_endpoints.pop(name, None)
        save_json(ENDPOINTS_PATH, self.saved_endpoints)
        self._refresh_endpoint_presets()
        self._refresh_provider_values()

    def _profile_data(self) -> dict[str, Any]:
        return {
            "host": self.host_var.get().strip(),
            "port": self.port_var.get(),
            "username": self.user_var.get().strip(),
            "auth_method": self.auth_var.get(),
            "key_path": self.key_path_var.get().strip(),
            "trust_new_host": self.trust_host_var.get(),
            "use_sudo": self.use_sudo_var.get(),
            "config_path": self.remote_config_var.get().strip(),
            "env_path": self.remote_env_var.get().strip(),
            "service": self.service_var.get().strip(),
        }

    def save_ssh_profile(self) -> None:
        name = self.profile_var.get().strip() or f"{self.user_var.get()}@{self.host_var.get()}"
        if not self.host_var.get().strip() or not self.user_var.get().strip():
            self._show_error("Host and username are required")
            return
        self.saved_profiles[name] = self._profile_data()
        save_json(SSH_PROFILES_PATH, self.saved_profiles)
        self.profile_var.set(name)
        self._refresh_profiles()
        self._set_status(f"Saved SSH profile {name}; secrets excluded")

    def _refresh_profiles(self) -> None:
        if hasattr(self, "profile_combo"):
            self.profile_combo.configure(values=sorted(self.saved_profiles))

    def load_ssh_profile(self, _event: Any = None) -> None:
        profile = self.saved_profiles.get(self.profile_var.get())
        if not isinstance(profile, dict):
            return
        self.host_var.set(profile.get("host", ""))
        self.port_var.set(profile.get("port", 22))
        self.user_var.set(profile.get("username", ""))
        self.auth_var.set(profile.get("auth_method", "agent"))
        self.key_path_var.set(profile.get("key_path", "~/.ssh/id_ed25519"))
        self.trust_host_var.set(bool(profile.get("trust_new_host", False)))
        self.use_sudo_var.set(bool(profile.get("use_sudo", False)))
        self.remote_config_var.set(profile.get("config_path", str(DEFAULT_CONFIG_PATH)))
        self.remote_env_var.set(profile.get("env_path", str(DEFAULT_ENV_PATH)))
        self.service_var.set(profile.get("service", "butter"))
        self.password_var.set("")
        self.key_passphrase_var.set("")
        self.sudo_password_var.set("")

    def delete_ssh_profile(self) -> None:
        name = self.profile_var.get()
        self.saved_profiles.pop(name, None)
        save_json(SSH_PROFILES_PATH, self.saved_profiles)
        self.profile_var.set("")
        self._refresh_profiles()

    def close(self) -> None:
        self.ssh.disconnect()
        self.root.destroy()


class RemoteBrowserDialog:
    def __init__(
        self,
        parent: Any,
        ssh: SSHManager,
        start_path: str,
        use_sudo: bool,
        tk: Any,
        ttk: Any,
        messagebox: Any,
    ) -> None:
        self.ssh = ssh
        self.use_sudo = use_sudo
        self.tk = tk
        self.messagebox = messagebox
        self.selected_path: str | None = None
        self.current_path = validate_remote_path(start_path)
        self.window = tk.Toplevel(parent)
        self.window.title("Remote File Browser")
        self.window.geometry("720x500")
        self.window.transient(parent)
        self.window.grab_set()
        row = ttk.Frame(self.window, padding=8)
        row.pack(fill=tk.X)
        self.path_var = tk.StringVar(value=self.current_path)
        ttk.Entry(row, textvariable=self.path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Go", command=self.navigate).pack(side=tk.LEFT, padx=5)
        ttk.Button(row, text="Up", command=self.go_up).pack(side=tk.LEFT)
        self.tree = ttk.Treeview(self.window, columns=("type", "size"), show="tree headings")
        self.tree.heading("#0", text="Name")
        self.tree.heading("type", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8)
        self.tree.bind("<Double-1>", self.open_selected)
        buttons = ttk.Frame(self.window, padding=8)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Select File", command=self.select_file).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side=tk.RIGHT, padx=5)
        self.navigate()

    def navigate(self) -> None:
        try:
            target = validate_remote_path(self.path_var.get())
            entries = self.ssh.list_directory(target, use_sudo=self.use_sudo)
            self.tree.delete(*self.tree.get_children())
            for item in entries:
                self.tree.insert(
                    "",
                    self.tk.END,
                    text=item["name"],
                    values=("Directory" if item["is_dir"] else "File", item["size"]),
                )
            self.current_path = target
            self.path_var.set(target)
        except Exception as exc:
            self.messagebox.showerror(APP_NAME, str(exc), parent=self.window)

    def go_up(self) -> None:
        self.path_var.set(str(PurePosixPath(self.current_path).parent))
        self.navigate()

    def open_selected(self, _event: Any = None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        item = self.tree.item(selected[0])
        target = str(PurePosixPath(self.current_path) / item["text"])
        if item["values"][0] == "Directory":
            self.path_var.set(target)
            self.navigate()
        else:
            self.selected_path = target
            self.window.destroy()

    def select_file(self) -> None:
        selected = self.tree.selection()
        if selected:
            item = self.tree.item(selected[0])
            if item["values"][0] == "File":
                self.selected_path = str(PurePosixPath(self.current_path) / item["text"])
                self.window.destroy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    commands = parser.add_subparsers(dest="command")

    gui = commands.add_parser("gui", help="Open desktop configuration manager")
    gui.add_argument("--config", help="Local config path", default="config.yaml")
    gui.add_argument("--env", help="Local environment path", default=".env")
    gui.add_argument("--host", help="Remote SSH host")
    gui.add_argument("--port", type=int, default=22)
    gui.add_argument("--user", help="Remote SSH username")
    gui.add_argument("--key", help="Remote SSH private-key path")
    gui.add_argument("--remote-config", default=str(DEFAULT_CONFIG_PATH))
    gui.add_argument("--remote-env", default=str(DEFAULT_ENV_PATH))

    discover = commands.add_parser("discover", help="Print model IDs from one endpoint")
    discover.add_argument("--base-url", required=True)
    discover.add_argument("--api-key")
    discover.add_argument("--api-key-env")

    sync = commands.add_parser("sync", help="Refresh Butter routing.models from providers")
    sync.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sync.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH)
    sync.add_argument("--provider", action="append", dest="providers")
    sync.add_argument("--no-prune", action="store_true")
    sync.add_argument("--dry-run", action="store_true")

    summary = commands.add_parser("summary", help="Print redacted config summary")
    summary.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    validate_cmd = commands.add_parser("validate", help="Validate Butter configuration syntax")
    validate_cmd.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    add_route_cmd = commands.add_parser("add-route", help="Add or update a routed model entry")
    add_route_cmd.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    add_route_cmd.add_argument("--provider", help="Single provider name (must be in providers block)")
    add_route_cmd.add_argument("--providers", nargs="+", help="Multiple provider names for shared model failover")
    add_route_cmd.add_argument("--model", required=True, help="Upstream model ID")
    add_route_cmd.add_argument("--route", help="Prefix route ID (defaults to <provider>/<model>)")
    add_route_cmd.add_argument("--label", help="Curated human label")
    add_route_cmd.add_argument("--family", help="Architecture family (e.g. qwen, llama, kimi)")
    add_route_cmd.add_argument("--strategy", default="priority", help="Strategy (default: priority)")
    add_route_cmd.add_argument("--extra-body", help="JSON string of arbitrary extra parameters to inject (e.g. ZDR)")

    list_routes_cmd = commands.add_parser("list-routes", help="List all configured routes and their metadata")
    list_routes_cmd.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    list_routes_cmd.add_argument("--json", action="store_true", help="Output as JSON")

    remove_route_cmd = commands.add_parser("remove-route", help="Remove a routed model entry")
    remove_route_cmd.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    remove_route_cmd.add_argument("--route", required=True, help="Route ID to remove")

    gen_tmpl = commands.add_parser("generate-template", help="Generate a clean starter Butter config")
    gen_tmpl.add_argument("--output", type=Path, default=Path("config.template.yaml"))
    return parser


def run_gui(args: argparse.Namespace) -> int:
    require_core_dependencies()
    tk, _, _, _, _ = import_tkinter()
    root = tk.Tk()
    app = ButterConfigGUI(root, args)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()
    return 0


def run_cli(args: argparse.Namespace) -> int:
    if args.command == "generate-template":
        args.output.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        print(f"Generated config template: {args.output}")
        return 0
    require_core_dependencies()
    if args.command == "discover":
        api_key = args.api_key or ""
        if args.api_key_env:
            api_key = os.environ.get(args.api_key_env, "")
        print(json.dumps(discover_models(args.base_url, api_key), indent=2))
        return 0
    if args.command in {"summary", "validate"}:
        config = load_config_file(args.config)
        validate_butter_config(config, strict=True)
        if args.command == "summary":
            print(json.dumps(config_summary(config), indent=2))
        else:
            print(f"OK: {args.config}")
        return 0
    if args.command == "sync":
        config = load_config_file(args.config)
        result = sync_catalogs(
            config,
            load_env_file(args.env),
            selected_providers=set(args.providers) if args.providers else None,
            prune=not args.no_prune,
        )
        if not args.dry_run:
            atomic_write_text(args.config, dump_config(config), backup=True)
        result["dry_run"] = bool(args.dry_run)
        result["config_path"] = str(args.config)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "add-route":
        config = load_config_file(args.config)
        extra_body = None
        if getattr(args, "extra_body", None):
            try:
                extra_body = json.loads(args.extra_body)
            except json.JSONDecodeError as exc:
                raise ButterConfigError(f"Invalid JSON for --extra-body: {exc}") from exc
            if not isinstance(extra_body, dict):
                raise ButterConfigError("--extra-body must parse to a JSON object")
        route_id = apply_route(
            config,
            provider=args.provider,
            providers=args.providers,
            model=args.model,
            route_id=args.route,
            label=args.label,
            family=args.family,
            strategy=args.strategy,
            extra_body=extra_body,
        )
        validate_butter_config(config, strict=True)
        atomic_write_text(args.config, dump_config(config), backup=True)
        prov_desc = f"providers: {args.providers}" if args.providers else f"provider: {args.provider}"
        print(f"Added route: {route_id} -> {prov_desc}, model: {args.model}")
        return 0
    if args.command == "list-routes":
        config = load_config_file(args.config)
        routes = list_routes(config)
        if getattr(args, "json", False):
            print(json.dumps(routes, indent=2))
        else:
            print(f"{'ROUTE ID':<35} {'PROVIDER':<15} {'UPSTREAM MODEL':<30} {'LABEL':<25} {'FAMILY':<10}")
            print("-" * 115)
            for r in routes:
                print(f"{r['route_id']:<35} {r['provider']:<15} {r['model']:<30} {r['label']:<25} {r['family']:<10}")
        return 0
    if args.command == "remove-route":
        config = load_config_file(args.config)
        if remove_route(config, args.route):
            atomic_write_text(args.config, dump_config(config), backup=True)
            print(f"Removed route: {args.route}")
        else:
            print(f"Route not found: {args.route}", file=sys.stderr)
            return 1
        return 0
    if args.command == "generate-template":
        args.output.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
        print(f"Generated config template: {args.output}")
        return 0
    return run_gui(args)


def has_display() -> bool:
    if sys.platform.startswith("win"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"gui", "discover", "sync", "summary", "validate", "add-route", "list-routes", "remove-route", "generate-template"}
    if not argv or (argv[0] not in commands and not argv[0].startswith("-")):
        if has_display():
            argv.insert(0, "gui")
        else:
            argv = ["--help"]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_cli(args)
    except ButterConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
