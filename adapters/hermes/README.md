# Hermes adapter

Hermes는 pip entry point로 ForgetForge를 로드합니다.

```bash
hermes plugins enable forgetforge
forgetforge init --agents=hermes
```

## 연결된 AI 도구

| Tool | 용도 |
|------|------|
| `forgetforge_store` | 저장/갱신 (contradiction warnings) |
| `forgetforge_recall` | FTS 검색 + retrieval 기록 (`layer` 필수) |
| `forgetforge_status` | tier·건강 상태 |
| `forgetforge_keep` | `#keep_forever` |
| `forgetforge_forget` | `#forget_this` |
| `forgetforge_import_brief` | preprocessing/supercoder brief 수입 |
| `forgetforge_hot_context` | hot tier 블록 (또는 `pre_llm_call` hook) |

연결된 AI는 recall 결과를 읽고 응답 맥락에 반영합니다. Hermes는 hot tier를 `pre_llm_call` hook으로 자동 inject합니다.