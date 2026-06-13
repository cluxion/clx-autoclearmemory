---
name: forgetforge
description: Recall-centric memory for agent sessions. Use when context grows, when prior facts are needed, or when user tags #keep_forever / #forget_this. You (the connected AI) call forgetforge tools/CLI — the plugin does not call a separate model.
---

# ForgetForge — 연결된 AI 지시문

ForgetForge는 **당신(연결된 AI)** 이 도구·CLI를 호출해 메모리를 관리합니다. 플러그인은 점수·tier·DB만 제공합니다.

## 설치 확인

```bash
forgetforge check
```

## 언제 호출할지

| 상황 | 동작 |
|------|------|
| 새 사실·결정 저장 | `forgetforge store <id> --content "..."` 또는 `forgetforge_store` |
| 세션 시작·맥락 비대 | `forgetforge status` |
| 과거 사실 필요 | `forgetforge recall <topic>` 또는 `forgetforge_recall` layer=`explicit` |
| 응답에 기억을 실제 사용 | `forgetforge_recall` layer=`implicit` |
| 세션 마무리 | 사용한 기억에 대해 `forgetforge_recall` layer=`reflection` |
| 사용자 `#keep_forever` | `forgetforge keep <id>` |
| 사용자 `#forget_this` | `forgetforge forget <id>` |
| preprocessing/supercoder brief 수신 | `forgetforge import-brief` 또는 `forgetforge_import_brief` |
| store 후 `contradiction_warnings` | 기존 기억과 충돌 여부를 사용자에게 알리고 reconcile |

## 규칙

1. **회상이 강화 신호** — 저장만으로는 강해지지 않음. 사용·recall 시 layer를 기록할 것.
2. Tier: Hot → Warm (episodic/semantic/procedural) → Cold. Cold는 recall 시에만 다시 가져올 것.
3. recall 결과를 읽고 **당신의 응답 맥락에 반영**할 것. 플러그인이 응답을 대신 작성하지 않음.
4. 모르는 사실을 기억에서 찾지 못하면 추측하지 말 것.
5. Hermes에서는 hot tier가 `pre_llm_call` hook으로 자동 inject됩니다. 수동이 필요하면 `forgetforge_hot_context`를 호출하세요.

## Retention (참고)

R = e^{-t/S} × (1 + 0.45·N_r + 0.30·I + 0.25·F), S = ln(1 + N_r).  
Scoring은 Rust `forgetforge-engine`이 수행합니다.