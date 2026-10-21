# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities **privately** through GitHub's
[private vulnerability reporting](https://github.com/Flambeau-Phoenix/butter-modified-config/security/advisories/new).
Do not open a public issue for a security problem. Never include real API keys or
configs containing them; redact before sharing.

You can expect an acknowledgement within a few days.

## Scope

This repository contains a configuration manager, templates and a deploy script. It does
**not** contain the Butter proxy.

In scope: unsafe handling of credentials by the config manager (for example writing keys
to logs or summaries), unsafe file writes or path handling, command execution through
`deploy.sh`, and unsafe remote (SSH/SFTP) behavior.

Out of scope: vulnerabilities in the Butter proxy itself (report to
[Temikus/butter](https://github.com/Temikus/butter)), in upstream providers, or in Python
dependencies (report upstream).

## Handling keys safely

- Reference keys as `"${ENV_VAR}"` in config rather than writing them into files.
- Prefer `--api-key-env` over `--api-key`, which is recorded in shell history.
- `config.yaml`, `.env` files and key files are git-ignored; keep it that way.
- If a key was ever committed or pasted publicly, rotate it at the provider. Deleting it
  from Git history is not enough.
- Bind the gateway to `127.0.0.1` unless you need LAN access, and protect anything exposed
  with a firewall, VPN or authenticating reverse proxy.
