# Adding a Tool Provider

This guide explains how to extend A-LEMS with new tools an agent can
call during a task — search, calculators, database access, or
anything else your research needs an agent to invoke and have measured.

---

## 🔍 Tool Provider Architecture

Tool providers follow the same pattern as engine adapters: exact
identity-string match, not capability-probe selection. A provider
registers under `TOOL_PROVIDER_TYPE`, and one provider can expose any
number of individual tools.

- **Base class** `ToolProviderABC` (abstract)
  - `execute(tool_name, arguments, context)` → `ToolResult` (required)
  - `get_tools()` → `List[ToolDefinition]` (required)
  - `get_name()` → `str` (required)
  - `is_available()` → `bool` (required)
  - `get_config_schema()` → `Dict` (optional, defaults to empty)

### The built-in provider

A-LEMS ships one provider today, `BuiltinToolProvider`
(`TOOL_PROVIDER_TYPE = "builtin"`), exposing six tools: `calculator`,
`database_query`, `file_processor`, `web_search`, `code_executor`,
`api_query`. Read its source directly — it's the reference
implementation every example below is drawn from.

---

## 📋 Provider Requirements

### Mandatory Methods

| Method | Purpose | Returns |
|---|---|---|
| `execute(tool_name, arguments, context)` | Run one named tool | `ToolResult` |
| `get_tools()` | Describe every tool this provider exposes | `List[ToolDefinition]` |
| `get_name()` | Human-readable name for logs | `str` |
| `is_available()` | Can this provider run right now | `bool` |

`execute()` must never raise. Return
`ToolResult(success=False, ..., error="...")` on any failure,
including an unrecognized `tool_name` — the caller (the agent
runtime) relies on this contract to keep running after one tool call
fails.

### Optional Methods

| Method | Purpose | When to Implement |
|---|---|---|
| `get_config_schema()` | Declare `app_settings.yaml` config keys | Your provider needs configuration (an API key, a timeout, a base URL) |

---

## The ToolDefinition Contract

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]  # real JSON Schema, not a placeholder
```

`parameters` must be a real, complete JSON Schema object — this is
the contract any framework's tool-calling binding (LangChain's
`bind_tools()`, for instance) reads directly to construct a valid
function-calling request. A schema with an empty `properties` dict,
or one that doesn't match your `execute()` method's actual argument
names, will not fail loudly — it will produce silently wrong or
rejected tool calls at the framework layer, which is a much harder
bug to trace back here. Write the schema against your real method
signature, not from memory of what you intended it to accept.

Example, from the built-in `calculator` tool:

```python
ToolDefinition(
    name="calculator",
    description="Evaluate a math expression",
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate",
            },
        },
        "required": ["expression"],
    },
)
```

---

## The ToolExecutionContext Contract

```python
@dataclass(frozen=True)
class ToolExecutionContext:
    db_path: str
    run_id: Optional[int] = None
    agent_id: Optional[str] = None
```

This is passed to every `execute()` call. Notably, **it does not
carry an energy reader.** Tools never measure their own energy — the
harness measures tool execution energy by wrapping the call to
`execute()` with energy readings before and after, the same way it
measures an LLM call. If your tool genuinely needs to know it's
being measured (rare), that's a sign the measurement boundary
question deserves a second look before writing the tool, not a
reason to add a reader to this context.

---

## Building Your First Tool Provider

This example builds `alems-tool-serpapi`, a provider offering one
real web search tool backed by an external search API.

### Directory Structure

```
alems-tool-serpapi/
  pyproject.toml
  alems_tool_serpapi/
    __init__.py       # exports ALEMS_PLUGIN_META
    provider.py        # your ToolProviderABC implementation
```

### The Provider Class

```python
# alems_tool_serpapi/provider.py

import os
from typing import Any, Dict, List

from core.execution.tools.abc import (
    ToolDefinition, ToolExecutionContext, ToolProviderABC,
)
from core.execution.tools.real_tools import ToolResult


class SerpAPIToolProvider(ToolProviderABC):
    """Real web search backed by SerpAPI, replacing the built-in
    deterministic search stub for researchers who want live results."""

    TOOL_PROVIDER_TYPE = "serpapi"

    def execute(
        self, tool_name: str, arguments: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        if tool_name != "web_search":
            return ToolResult(
                success=False, result=None, tool_name=tool_name,
                duration_ns=0, error=f"Unknown tool: {tool_name}",
            )
        import time
        start = time.time_ns()
        try:
            # Real API call goes here. Kept out of this example —
            # see the SerpAPI client library's own docs for the call
            # shape. The pattern below is what matters.
            query = arguments.get("query", "")
            result_text = self._call_serpapi(query)
            return ToolResult(
                success=True, result=result_text, tool_name=tool_name,
                duration_ns=time.time_ns() - start,
            )
        except Exception as exc:
            # Never raise out of execute() — always return a ToolResult.
            return ToolResult(
                success=False, result=None, tool_name=tool_name,
                duration_ns=time.time_ns() - start, error=str(exc),
            )

    def get_tools(self) -> List[ToolDefinition]:
        return [
            ToolDefinition(
                name="web_search",
                description="Search the live web via SerpAPI",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                    },
                    "required": ["query"],
                },
            ),
        ]

    def get_name(self) -> str:
        return "serpapi"

    def is_available(self) -> bool:
        # Capability check only — is the API key present. Never raise here.
        return bool(os.environ.get("SERPAPI_KEY"))

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "timeout_ms": {
                "type": "int", "required": False, "default": 10000,
            },
        }
```

Notice this provider declares `web_search` — the same tool name the
built-in provider already exposes. That's allowed and intentional:
`TOOL_PROVIDER_TYPE` distinguishes providers, not tool names within
them. Whichever provider a task's tool registry lookup finds first
(built-in checked first, per the registration order) is the one that
runs — this is exactly how you'd measure "does a real search API use
more energy than the deterministic stub," a real comparison this
architecture was built to support.

### pyproject.toml

```toml
[project]
name = "alems-tool-serpapi"
version = "0.1.0"
dependencies = ["alems-platform>=1.0,<2.0", "google-search-results>=2.4"]

[project.entry-points."alems.tools"]
serpapi = "alems_tool_serpapi.provider:SerpAPIToolProvider"
```

### __init__.py

```python
ALEMS_PLUGIN_META = {
    "name": "serpapi",
    "version": "0.1.0",
    "alems_compat": ">=1.0,<2.0",
    "description": "Real web search via SerpAPI, replacing the built-in search stub",
    "platform_constraint": None,
}
```

---

## Testing Your Provider

No hardware or extension activation is needed to test a tool
provider directly — it's a pure Python object:

```python
from alems_tool_serpapi.provider import SerpAPIToolProvider
from core.execution.tools.abc import ToolExecutionContext

provider = SerpAPIToolProvider()
print("Available:", provider.is_available())

context = ToolExecutionContext(db_path="data/experiments.db")
result = provider.execute("web_search", {"query": "test query"}, context)
print(result)
```

Once installed (`pip install -e .` from your plugin's directory),
confirm A-LEMS actually discovers it — a clean import proves nothing
about registration:

```python
from core.execution.tools.bootstrap import tool_registry, register_all_tool_providers
register_all_tool_providers()
print(list(tool_registry.get_all().keys()))
# Expect: ['builtin', 'serpapi']
```

If your provider is missing from that list with no error printed,
check `is_available()` first — a provider that returns `False` there
is skipped silently by design, logged at `INFO` level, not treated as
a startup failure.

---

## What A-LEMS Guarantees

- Your provider cannot write to core measurement columns — it returns
  a `ToolResult`, and the harness decides what happens to it.
- Two providers can expose the same tool name with no conflict; only
  `TOOL_PROVIDER_TYPE` needs to be unique.
- A broken or unavailable provider never blocks startup unless a task
  explicitly requires it by name.
- Real JSON Schema in `get_tools()` is the same contract every
  framework adapter's tool-calling binding reads — write it once,
  correctly, and it works everywhere your provider is used.
