# ForgetForge Documentation

## 처음 읽는 분

**ForgetForge**는 에이전트 세션의 메모리가 무한히 쌓이는 문제를 막는 **망각·기억 강화 플러그인**입니다.

| 질문 | 답 |
|------|-----|
| **무엇을 하나요?** | 기억을 tier(Hot/Warm/Cold)로 나누고, **실제로 회상한 횟수**로 강도를 계산해 오래된 기억을 archive합니다. |
| **누가 실행하나요?** | **연결된 AI**(Hermes·Claude·Codex·Grok의 모델)가 skill/도구 지시에 따라 `forgetforge_*` 도구나 CLI를 호출합니다. |
| **플러그인이 모델을 부르나요?** | **아니요.** 점수·tier·DB만 관리합니다. 요약·반영·세션 정리는 **연결된 AI**가 수행합니다. |
| **왜 Rust인가요?** | retention 계산·tier 판정 hot path를 Rust로 두고, Python은 DB·adapter·CLI만 담당합니다. |

### 연결된 AI 사용 흐름

1. 세션 시작 또는 맥락이 커질 때 → `forgetforge status`로 건강 상태 확인
2. 과거 사실이 필요할 때 → `forgetforge recall <topic>` (explicit layer)
3. 응답에 기억을 실제로 썼다면 → `forgetforge_recall` with `layer: implicit`
4. 세션 마무리 시 사용한 기억을 점검했다면 → `forgetforge_recall` with `layer: reflection`
5. 사용자 `#keep_forever` / `#forget_this` → `forgetforge keep` / `forgetforge forget`
6. preprocessing/supercoder brief → `forgetforge import-brief` (또는 Hermes `forgetforge_import_brief`)
7. store 시 `contradiction_warnings`가 있으면 사용자에게 reconcile 제안

**v0.2 추가:** FTS5 recall, brief handoff, hot inject hook, contradiction hints, Parquet cold archive.

Skill 지시문: [`adapters/claude/skills/forgetforge/SKILL.md`](../adapters/claude/skills/forgetforge/SKILL.md)

### 사람(개발자)이 할 일

```bash
pip install cluxion-Agentplugin-AutoClearMemory
forgetforge init --agents=all   # DB + adapter assets + example config from wheel
hermes plugins enable forgetforge   # Hermes 예시
```

## 목차

| 문서 | 내용 |
|------|------|
| [architecture.md](architecture.md) | Recall-centric tiers, 데이터 흐름 |
| [design.md](design.md) | Retention 공식, retrieval layer, pruner |
| [installation.md](installation.md) | pip 설치, Rust 빌드, adapter |
| [agent-surfaces.md](agent-surfaces.md) | Hermes / Claude / Codex 연동 |
| [rust-architecture.md](rust-architecture.md) | Rust 메인 · Python bridge |

## 이 레포에서 다루지 않는 것

- API 키·OAuth (호스트 에이전트 소유)
- 플러그인 내부의 별도 LLM 호출
- 비공개 운영·배포 비밀

이슈는 GitHub Issues를 이용해 주세요.