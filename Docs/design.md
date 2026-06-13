# Design

## Retention formula

\[
R = e^{-t / S} \times \left(1 + 0.45 \cdot N_r + 0.30 \cdot I + 0.25 \cdot F \right)
\]

- \( t \): 마지막 회상 이후 경과일
- \( S = \ln(1 + N_r) \): Stability (회상 횟수 기반)
- \( N_r \): Retrieval count (Explicit + Implicit + Reflection)
- \( I \): Importance (0~1)
- \( F \): Frequency (secondary)

구현: `rust/forgetforge_engine/src/scoring.rs` (+ Python fallback in `rust_bridge.py`).

## Retrieval 3-layer (연결된 AI가 기록)

플러그인은 별도 LLM을 돌리지 않습니다. **연결된 AI**가 skill·도구 지시에 따라 `forgetforge_recall`을 호출할 때 `layer`로 구분합니다.

| Layer | 연결된 AI가 호출하는 시점 | Boost |
|-------|---------------------------|-------|
| `explicit` | `/recall`, 주제 검색, `forgetforge recall` | +0.45 |
| `implicit` | 응답에 해당 기억을 실제로 사용한 직후 | +0.35 |
| `reflection` | 세션 마무리 시 “이 기억을 썼는가?” 점검 후 | +0.25 |

모든 이벤트는 `retrieval_events` 테이블에 기록됩니다. recall 시 `importance`·`frequency`가 layer별로 소폭 증가합니다 (`db.bump_recall_stats`).

## FTS5 recall

- `memories_fts` (FTS5, porter tokenizer) — `search_memories`가 bm25 정렬
- FTS 실패·무결과 시 `LIKE` fallback
- upsert/forget 시 FTS 동기화
- `recall_query`: multi-match recall은 **one transaction** (per-row commit/fsync 방지)
- `score_memories`: listing/status hot-path는 **one engine call** (`decide_tier_batch`) per batch

## Brief handoff

`import_brief` — preprocessing/supercoder brief를 episodic memory로 저장:

```bash
forgetforge import-brief --source supercoder --brief "<json or text>"
```

Hermes: `forgetforge_import_brief`

## Contradiction hints

`store_memory`는 유사 토큰·부정 쌍(`always/never` 등)을 검사해 `contradiction_warnings`를 반환합니다. 연결된 AI가 사용자에게 reconcile을 제안합니다.

## Hot inject

- `hot_inject.build_hot_context` — hot tier 미리보기 블록
- Hermes `pre_llm_call` hook — hot 기억을 LLM 호출 전에 자동 주입

## Pruner (6시간 주기)

- Warm → Cold: \( R < 0.40 \)
- 180일 무회상 → `~/.forgetforge/archive/`에 parquet + jsonl + txt 저장 (`archive.write_cold_archive`)
- Cold → Warm: recall 발생 시 즉시 승격

주기 실행: `forgetforge prune` (cron/systemd 등).

## User safety tags

- `#keep_forever` → `forgetforge keep <id>`
- `#forget_this` → `forgetforge forget <id>`

## Package layout

```
src/forgetforge/          # db, recall, pruner, rust_bridge, cli
rust/forgetforge_engine/  # scoring + tier (Rust)
adapters/                 # Hermes README, Claude skill, Codex snippet
```