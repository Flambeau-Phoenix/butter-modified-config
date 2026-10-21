# Contributing

Thanks for helping. By taking part you agree to the [code of conduct](CODE_OF_CONDUCT.md).

## Set up

Python 3.10+.

```bash
git clone https://github.com/Flambeau-Phoenix/butter-modified-config.git
cd butter-modified-config
pip install -r requirements.txt
```

## Checks

```bash
python -m unittest test_model_limits -v
python butterproxy_config_manager.py validate --config config.example.yaml
python butterproxy_config_manager.py validate --config blank_config.yaml
bash -n deploy.sh
```

Add or update tests for behavior changes. `test_model_limits.py` shows the style: it loads
the manager as a module and exercises both the CLI and the helpers.

## Guidelines

- Keep the manager a single file with minimal dependencies (PyYAML and requests; paramiko
  stays optional).
- Keep templates and examples valid: they must pass `validate`.
- Never commit real keys, configs containing them, hostnames or personal paths. Use
  placeholders like `${PROVIDER_API_KEY}`.
- Be clear about which features need a Butter build with the extended schema versus stock
  upstream Butter.
- Update the README and CHANGELOG for user-visible changes.

## Issues and pull requests

Describe the problem, what you expected, your Python and OS versions and the smallest config
that reproduces it (with secrets removed). Say which checks you ran. Security problems go
through [SECURITY.md](SECURITY.md).
