# Installation

## pip

```bash
pip install cluxion-Agentplugin-AutoClearMemory
forgetforge init
forgetforge check
```

`forgetforge init` creates `~/.forgetforge/`, initializes the DB, copies Hermes adapter notes under `~/.forgetforge/adapters/`, and installs `config.yaml` from the wheel's example config (never overwrites an existing config).

데이터: `~/.forgetforge/` (`db.sqlite`, `config.yaml`, `archive/`).

## Rust engine (권장)

```bash
cargo build --release --manifest-path rust/forgetforge_engine/Cargo.toml
export FORGETFORGE_ENGINE_BIN="$(pwd)/rust/forgetforge_engine/target/release/forgetforge-engine"
forgetforge check
```

Rust가 없어도 **Python fallback**으로 scoring/tier가 동작합니다.

## Hermes

```bash
hermes plugins enable forgetforge
```

연결된 AI에게 `forgetforge_*` 도구 사용을 skill로 안내합니다.

## Claude Code

Install this repository as a Claude Code plugin. The root `.claude-plugin/plugin.json` exposes `commands/` and `skills/`; the host agent calls the `forgetforge` CLI.

## Codex

Install this repository as a Codex marketplace plugin:

```bash
codex plugin marketplace add <path-to-marketplace-root>
codex plugin add cluxion-agentplugin-autoclearmemory
```

The root `.codex-plugin/plugin.json` exposes `commands/` and `skills/`. The connected AI calls the `forgetforge` CLI:

```bash
forgetforge recall docker
forgetforge status
```

## Environment

| Variable | Purpose |
|----------|---------|
| `FORGETFORGE_HOME` | `~/.forgetforge` override |
| `FORGETFORGE_ENGINE_BIN` | `forgetforge-engine` 경로 |
