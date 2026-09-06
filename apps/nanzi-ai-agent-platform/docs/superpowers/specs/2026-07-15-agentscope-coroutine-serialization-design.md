# AgentScope Coroutine Serialization Fix Design

## Goal

Prevent AgentScope native file tools from losing state-injection metadata and make pending-confirmation snapshot failures identify any leaked coroutine precisely.

## Design

- The native-tool approval wrapper is covered by a regression test proving it already preserves AgentScope execution metadata through delegation; no production change is needed there.
- Pending snapshot serialization resolves leaked awaitables recursively at its asynchronous boundary before constructing the persisted snapshot. Resolution failures report the exact state path instead of coercing or dropping values.
- No changes are made to permission policy, tool output formats, or SSE contracts.

## Verification

- A wrapper test proves state-injection and concurrency metadata are preserved for native `Write`.
- A snapshot test proves a nested coroutine is awaited and persisted as its resolved value.
- Existing AgentScope tooling and pending-state tests remain green.
