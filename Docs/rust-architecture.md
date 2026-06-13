# Rust Architecture

## 원칙

ForgetForge는 **Rust가 메인**, **Python은 연결층**입니다.

```
연결된 AI (Hermes / Claude / Codex / Grok)
        ↓  skill / forgetforge_* tools / CLI
forgetforge                    (Python: DB, recall, pruner, CLI)
        ↓  subprocess JSON
forgetforge-engine             (Rust: retention, tier)
```

플러그인은 **연결된 AI에게 지시·도구·점수**를 제공합니다. 별도 LLM을 호출하지 않습니다.

## Rust: `forgetforge-engine`

| 명령 | 역할 |
|------|------|
| `score` | RetentionInput → retention, stability, boost |
| `tier` | TierInput → tier, action, retention |

```bash
cargo build --release --manifest-path rust/forgetforge_engine/Cargo.toml
```

## Python 역할

- SQLite schema (`memories`, `retrieval_events`)
- recall layer 기록 (`explicit` / `implicit` / `reflection`)
- pruner orchestration
- Hermes `register()`, CLI
- Rust 미설치 시 동일 공식 Python fallback