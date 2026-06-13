# Installation

## pip (모든 에이전트)

```bash
pip install cluxion-Agentplugin-AutoClearMemory
forgetforge init --agents=all
forgetforge check
```

`forgetforge init` creates `~/.forgetforge/`, initializes the DB, copies packaged adapter assets under `~/.forgetforge/adapters/`, and installs `config.yaml` from the wheel's example config (never overwrites an existing config).

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

`adapters/claude/skills/forgetforge/`를 skills 경로에 추가하거나, `adapters/claude/.claude-plugin/` manifest를 사용합니다.

## Codex

`adapters/codex/mcp-snippet.toml`을 참고합니다. 연결된 AI는 `forgetforge` CLI를 터미널에서 호출합니다.

```bash
forgetforge recall docker
forgetforge status
```

## Environment

| Variable | Purpose |
|----------|---------|
| `FORGETFORGE_HOME` | `~/.forgetforge` override |
| `FORGETFORGE_ENGINE_BIN` | `forgetforge-engine` 경로 |