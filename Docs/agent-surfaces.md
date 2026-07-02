# Agent Surfaces

ForgetForge는 **하나의 pip 패키지**로 Hermes · Claude Code · Codex · Grok Build를 커버합니다.  
Codex와 Claude Code는 같은 루트 플러그인 artifact를 설치하고, **core(Rust + SQLite)는 공유**합니다.

**공통 원칙:** 연결된 AI가 skill/도구 지시에 따라 recall·status·keep·forget을 호출합니다. 플러그인은 LLM을 호출하지 않습니다.

## Hermes

- Entry: `[project.entry-points."hermes_agent.plugins"]`
- 활성화: `hermes plugins enable forgetforge`
- Tools: `forgetforge_store`, `forgetforge_recall`, `forgetforge_status`, `forgetforge_keep`, `forgetforge_forget`, `forgetforge_import_brief`, `forgetforge_hot_context`
- `forgetforge_recall`의 `layer`: `explicit` | `implicit` | `reflection`
- `pre_llm_call` hook: hot tier 자동 inject

## Claude Code

- Manifest: `.claude-plugin/plugin.json`
- Skill: `skills/forgetforge/SKILL.md`
- Commands: `commands/forgetforge-*.md`
- 연결된 AI는 skill 규칙에 따라 CLI 또는 동일 semantics의 도구 호출
- 세션 마무리 reflection도 **Claude 모델**이 skill 지시에 따라 `layer: reflection` recall 기록

## Codex

- Manifest: `.codex-plugin/plugin.json`
- Skill: `skills/forgetforge/SKILL.md`
- Commands: `commands/forgetforge-*.md`
- 연결된 AI는 터미널에서 `forgetforge` CLI를 호출 (recall/status/keep/forget)
- 규칙은 Claude skill과 동일

## Grok Build

- `forgetforge` CLI + 동일 recall 규칙
- 프로젝트 skill에 `skills/forgetforge/SKILL.md` 내용을 참고해 연동

## 공통 명령

| Command | Description |
|---------|-------------|
| `forgetforge init` | DB + Hermes adapter notes + example config from wheel |
| `forgetforge recall <query>` | Explicit retrieval |
| `forgetforge keep <id>` | `#keep_forever` |
| `forgetforge forget <id>` | `#forget_this` |
| `forgetforge status` | Memory health |
| `forgetforge store <id> --content "..."` | Store/update memory |
| `forgetforge import-brief` | Brief handoff |
| `forgetforge hot-context` | Hot tier block |
| `forgetforge prune` | Pruner 1회 |
| `forgetforge pruner-daemon --max-cycles 24` | Bounded background pruner |
