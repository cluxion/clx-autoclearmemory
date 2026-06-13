========= Written in Korean first, then English ==========

======== 한국어 ========

# cluxion-Agentplugin-AutoClearMemory

AI 에이전트(Hermes Agent, Claude Code, Codex)를 위한 기억 플러그인입니다. 사람의 기억처럼 동작하는
장기 기억을 에이전트에 부여합니다: 자주 회상하는 사실은 또렷하게 남고, 전혀 쓰지 않는 사실은 흐려져
보관됩니다. 가장 관련 있는 기억은 모델이 답하기 전에 자동으로 떠오릅니다.

## 설치

```bash
pip install cluxion-Agentplugin-AutoClearMemory
forgetforge init            # ~/.forgetforge 를 설정합니다 (데이터베이스 + 설정)
```

### Hermes Agent에서 사용

`~/.hermes/config.yaml` 에 추가한 뒤 Hermes를 재시작하세요.

```yaml
plugins:
  enabled:
    - cluxion-agentplugin-autoclearmemory
```

## 사용

에이전트가 자동으로 호출하거나, CLI로 직접 사용할 수 있습니다.

```bash
forgetforge store redis-port --content "Redis runs on port 6380" --importance 0.8
forgetforge recall redis        # 기억을 꺼내고(회상) 강화합니다
forgetforge keep redis-port     # 절대 흐려지지 않도록 기억을 고정합니다
forgetforge forget redis-port   # 기억이 흐려지도록 둡니다
forgetforge status              # 무엇이 기억되어 있는지 확인합니다
```

Hermes에서는 동일한 동작이 `forgetforge_*` 도구로 제공되며, 가장 관련 있는("hot") 기억이 매 모델
호출 전에 컨텍스트에 추가됩니다.

## 라이선스

Apache-2.0

============ English ==========

# cluxion-Agentplugin-AutoClearMemory

A memory plugin for AI agents (Hermes Agent, Claude Code, Codex). It gives your agent a
long-term memory that behaves like human memory: facts you recall often stay sharp, facts you
never use fade and get archived. The most relevant memories are surfaced automatically before
the model answers.

## Install

```bash
pip install cluxion-Agentplugin-AutoClearMemory
forgetforge init            # sets up ~/.forgetforge (database + config)
```

### Use with Hermes Agent

Add it to `~/.hermes/config.yaml`, then restart Hermes:

```yaml
plugins:
  enabled:
    - cluxion-agentplugin-autoclearmemory
```

## Use

Your agent calls these automatically, or you can use the CLI directly:

```bash
forgetforge store redis-port --content "Redis runs on port 6380" --importance 0.8
forgetforge recall redis        # retrieve a memory (and reinforce it)
forgetforge keep redis-port     # pin a memory so it never fades
forgetforge forget redis-port   # let a memory fade away
forgetforge status              # see what's remembered
```

In Hermes the same actions are available as `forgetforge_*` tools, and your most relevant
("hot") memories are added to the context before each model call.

## License

Apache-2.0
