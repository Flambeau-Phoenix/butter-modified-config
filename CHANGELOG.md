# Changelog

Earlier history is in the Git log. The config manager reports its own version
(`butterproxy_config_manager.py --version`, currently 1.1.0).

## Unreleased

### Added

- Apache-2.0 `LICENSE` file (the README previously showed a license badge with no license
  file in the repository).
- `requirements.txt`, security policy, code of conduct, contributing guide, issue and
  pull-request templates, Dependabot for GitHub Actions, and a CI workflow that runs the
  tests, validates the example configs and syntax-checks `deploy.sh` on Linux and Windows.

### Changed

- README rewritten. It now states plainly that this repository holds the configuration
  tooling and not the Butter proxy, credits upstream Butter, and notes that the extended
  schema (`label`, `family`, `extra_body`, `context_length`, `/api/models`) needs a Butter
  build that implements it. It no longer tells readers to build `./cmd/butter` from this
  repository, which does not contain it. The manager's full command list, the GUI and
  `deploy.sh` behavior are documented, with a security section.
