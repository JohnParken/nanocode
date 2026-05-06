# pi-mono Integration Notes

These notes help place a custom Qwen wrapper provider into `pi-mono`.

## Likely Files To Mirror

- `packages/ai/src/types.ts`
- `packages/ai/src/providers/openai-completions.ts`

## Recommended New File

- `packages/ai/src/providers/qwen-wrapper.ts`

## Suggested API Identifier

Use a custom API string such as:

- `"qwen-wrapper"`

This avoids pretending the wrapper is fully OpenAI-compatible.

## Suggested Exports

Export at least:

- `streamQwenWrapper`
- `streamSimpleQwenWrapper`

If the repo needs a provider registry update, add the new exports in the same place other
providers are re-exported or registered.

## Suggested Internal Sections

Keep this rough order in the TS provider file:

1. imports
2. options and chunk interfaces
3. request template types
4. prompt helpers
5. message serializers
6. tool schema builder
7. text tool extraction helpers
8. chunk parsing helpers
9. main stream function
10. simple wrapper function

## Prompt Placement

Prefer these auxiliary files when the user wants maintainable code:

- `packages/ai/src/providers/qwen-wrapper-prompts/shared.ts`
- `packages/ai/src/providers/qwen-wrapper-prompts/text.ts`

If the user wants a single-file provider for bootstrapping, embed prompt strings but keep
the helper functions clearly separated and labeled.

## Tests To Add In pi-mono

At minimum, test:

- request construction in text mode
- text prompt includes `<tool_call>` protocol
- unsupported tool-calling modes resolve to text
- text parser recovers wrapped JSON tool calls
