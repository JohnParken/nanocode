---
name: qwen-provider-api
description: Use when generating, modifying, or reviewing a Qwen provider that talks to a custom wrapper API, uses a fixed wrapper payload shape, and injects tool definitions through the first system prompt message in text mode.
---

# Qwen Provider API

Use this skill when you need to generate or modify a Qwen provider in any project, as long as the target provider follows these two core rules:

1. It must send requests to a custom wrapper API with a fixed payload shape.
2. It must inject tool definitions and tool-calling protocol through the first `system` message, not through a top-level API `tools` field.

This skill is intentionally independent of any one codebase. Treat it as a design and implementation guide for building the provider in a new project.

## Main Goal

This skill exists to preserve two implementation constraints:

### 1. Fixed wrapper API payload shape

The provider must build requests in this wrapper-oriented shape:

- `token`
- `apikey`
- `type`
- `modelId`
- `appInfo`
- `variable`
- `data.messages`
- `data.stream`

Do not rewrite this into an OpenAI-style payload such as:

- `{ model, messages, tools, stream }`

If a project already has OpenAI providers, do not copy their request format into this provider.

### 2. Tool injection through system prompt

The provider must use text-mode tool calling.

That means:

- omit `data.tools`
- build a full tool-use protocol prompt
- inject that prompt into the first `system` message
- require the model to emit text protocol tool calls like:
  - `<tool_call>{"name":"...","arguments":{...}}</tool_call>`

The provider may keep wrapper template placeholders such as `(tools)` or `variable.tools` for compatibility, but those placeholders are not the primary tool-injection path.

## When To Use This Skill

Use this skill when the task involves any of these:

- generating a new Qwen provider for a custom wrapper API
- updating request construction for the wrapper payload shape
- updating system-prompt-based tool injection
- updating text-mode tool-call extraction
- documenting or reviewing how the provider interacts with the model

## Required Request Shape

Build requests around this structure:

```json
{
  "token": "dashscope-token",
  "apikey": "wrapper-app-key",
  "type": "txt",
  "modelId": "lightapplication",
  "appInfo": {
    "agent_id": "wrapper-agent-id",
    "sensitive_judge": false,
    "safe_model_judge": false,
    "max_new_tokens": 81920,
    "temperature": 0.3,
    "name": "Chat-Medium",
    "prompt": "(static_memory)\n(tools)"
  },
  "variable": [
    {
      "name": "static_memory",
      "value": "Optional reusable context"
    },
    {
      "name": "tools",
      "value": ""
    }
  ],
  "data": {
    "messages": [],
    "stream": true
  }
}
```

### Request construction rules

- `token` comes from the provider API key or wrapper auth token.
- `apikey` is optional and belongs to the wrapper layer, not the model API itself.
- `modelId` must be set explicitly from the selected model.
- `appInfo` is wrapper configuration. Preserve its shape.
- `variable` is a wrapper template variable list. It may include placeholders like:
  - `static_memory`
  - `tools`
- `data.messages` is the actual conversational history sent to the model.
- `data.stream` should stay aligned with the wrapper's streaming contract.

## Tool Injection Through System Prompt

The provider must construct one full text-mode tool prompt and place it in the first `system` message.

That prompt should contain:

- the user-supplied system prompt
- shared tool-use rules
- text-mode tool protocol rules
- a `<tools>...</tools>` block containing all tool schemas
- optional few-shot examples
- a final reminder about emitting `<tool_call>` blocks

### Expected message shape

The request should look like this when tools are present:

```json
{
  "data": {
    "messages": [
      {
        "role": "system",
        "content": "You are a tool-using assistant.\n\n# Shared Tool Use Rules\n...\n\n# Tools\n\n<tools>\n{\n  \"type\": \"function\",\n  \"function\": {\n    \"name\": \"read_file\",\n    \"description\": \"Read exact file contents with line numbers.\",\n    \"parameters\": {\n      \"type\": \"object\",\n      \"properties\": {\n        \"path\": {\"type\": \"string\"},\n        \"start_line\": {\"type\": \"integer\"}\n      }\n    }\n  }\n}\n</tools>\n\nIf a tool is needed, output one or more <tool_call>{\"name\":\"...\",\"arguments\":{...}}</tool_call> blocks."
      },
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "Read config and summarize it."
          }
        ]
      }
    ]
  }
}
```

### Important rules

- The first `system` message is the primary tool-registration mechanism.
- Do not rely on top-level request `tools`.
- Do not describe `variable.tools` as the main carrier of tool schemas if the implementation uses `system` message injection.
- Keep prompt assembly outside transport code when possible.

## Tool Schema Format

Each tool should be converted to a function-schema JSON object like this:

```json
{
  "type": "function",
  "function": {
    "name": "edit_file",
    "description": "Edit a single file using exact text replacement.",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string"
        },
        "edits": {
          "type": "array"
        }
      },
      "required": ["path"]
    }
  }
}
```

These schema objects must be rendered into the `<tools>` block inside the first `system` message.

## Message Mapping Rules

The provider should serialize messages with these rules:

### System message

- The first message must be the fully assembled tool prompt.

### User messages

- User content may be serialized as structured content arrays.
- Example:
  - `{"role":"user","content":[{"type":"text","text":"Read config"}]}`

### Assistant history

- Preserve normal assistant text.
- Preserve prior assistant tool calls by replaying them back to the model as text protocol blocks.
- A prior tool call should be serialized like:

```text
<tool_call>{"name":"read_file","arguments":{"path":"app.py","start_line":1},"id":"call_1"}</tool_call>
```

Do not drop prior tool calls from assistant history. That makes the model lose track of what it already requested.

### Tool results

- Tool results should map to `role="tool"` messages.
- Preserve `tool_call_id`.
- Serialize the tool result content back to the model.

Example:

```json
{
  "role": "tool",
  "tool_call_id": "call_1",
  "content": [
    {
      "type": "text",
      "text": "FILE: app.py\nLINES: 1-5 of 42\n1: def main(): ..."
    }
  ]
}
```

## Expected Tool Protocol

The text protocol should instruct the model to emit tool calls in this exact form:

```text
<tool_call>{"name":"<tool-name>","arguments":{...}}</tool_call>
```

Rules:

- one tool call per `<tool_call>` block
- each block must contain valid JSON
- `name` must match one of the tools in `<tools>`
- `arguments` must be a JSON object
- value types must match the tool schema
- when multiple tools are needed, output multiple `<tool_call>` blocks

## Response Parsing Rules

Support both of these response families.

### 1. Wrapper `status/result` chunks

Fields may include:

- `status`
- `result`
- `resCode`
- `resMessage`
- `questionId`
- `sessionId`

Rules:

- append each `result` fragment in arrival order
- treat `running` and `success` as non-terminal fragments
- finalize only on an explicit terminal state such as `completed`
- after the full text is assembled, extract `<tool_call>...</tool_call>`

### 2. OpenAI-like `output.choices` chunks

Fields may include:

- `output.choices[0].message.content`
- `output.choices[0].message.tool_calls`
- `finish_reason`

Rules:

- preserve visible text
- if structured `tool_calls` appear, aggregate streamed fragments
- if structured tool calls are absent, still parse embedded text protocol calls from the final text

## Tool Call Extraction Rules

The parser should remain defensive.

Preserve these behaviors:

- normalize BOM and Windows newlines
- strip markdown fences around candidate JSON
- parse `<tool_call>...</tool_call>` first
- if no tags are found, try raw JSON candidates from free text
- accept:
  - direct `{"name":"...","arguments":{...}}`
  - `{"tool_call": {...}}`
  - `{"tool_calls":[...]}`
- accept arguments as either:
  - JSON object
  - JSON string containing an object
  - recoverable Python-literal-like object string
- reject tool names not present in the available tool set

## Implementation Checklist

When generating provider code, make sure all of these are true:

- request payload matches the fixed wrapper contract
- top-level `data.tools` is absent in text mode
- the first `system` message contains the full tool protocol
- tool schemas are rendered into `<tools>`
- prior assistant tool calls are replayed as `<tool_call>` text
- tool results are sent back as `role="tool"` messages
- final text is scanned for tool calls before ending the turn

## What To Avoid

- do not convert the provider to OpenAI request shape
- do not rely on native function-calling registration for this provider
- do not document `variable.tools` as the primary registration path if the real path is the first `system` message
- do not drop prior tool calls from assistant history
- do not let examples disagree with runtime message roles
- do not hide prompt assembly inside transport code without documenting the final system-prompt structure
