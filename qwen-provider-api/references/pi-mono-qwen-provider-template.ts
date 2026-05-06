/**
 * Self-contained Qwen wrapper provider skeleton for pi-mono style TypeScript providers.
 *
 * If this file is copied into `packages/ai/src/providers/`, replace the package imports with:
 * - `../types.js`
 * - `../utils/event-stream.js`
 */

import type {
	AssistantMessage,
	Context,
	Message,
	Model,
	SimpleStreamOptions,
	StopReason,
	StreamFunction,
	StreamOptions,
	TextContent,
	ThinkingContent,
	Tool,
	ToolCall,
	ToolResultMessage,
} from "@mariozechner/pi-ai";
import { AssistantMessageEventStream } from "@mariozechner/pi-ai";

export interface QwenWrapperOptions extends StreamOptions {
	apiKey?: string;
	appApiKey?: string;
	baseUrl?: string;
	timeout?: number;
	toolCallingMode?: "auto" | "native" | "text";
	requestTemplate?: QwenRequestTemplate;
}

interface QwenRequestTemplate {
	token?: string;
	apikey?: string;
	type?: string;
	modelId?: string;
	appInfo?: Record<string, unknown>;
	variable?: Array<{ name: string; value: string }>;
	data?: {
		messages?: unknown[];
		tools?: unknown[];
		stream?: boolean;
		[key: string]: unknown;
	};
	[key: string]: unknown;
}

interface QwenChunk {
	status?: string;
	result?: string;
	resCode?: string;
	resMessage?: string;
	questionId?: string;
	sessionId?: string;
	output?: {
		finish_reason?: string;
		choices?: Array<{
			finish_reason?: string;
			message?: Record<string, any>;
			delta?: Record<string, any>;
		}>;
	};
	usage?: Record<string, any>;
}

const DEFAULT_BASE_URL =
	"https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation";

export const streamQwenWrapper: StreamFunction<"qwen-wrapper", QwenWrapperOptions> = (
	model: Model<"qwen-wrapper">,
	context: Context,
	options?: QwenWrapperOptions,
) => {
	const stream = new AssistantMessageEventStream();

	(async () => {
		const output: AssistantMessage = {
			role: "assistant",
			content: [],
			api: model.api,
			provider: model.provider,
			model: model.id,
			usage: {
				input: 0,
				output: 0,
				cacheRead: 0,
				cacheWrite: 0,
				totalTokens: 0,
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
			},
			stopReason: "stop",
			timestamp: Date.now(),
		};

		try {
			const textAttempt = await collectAttempt(model, context, {
				...options,
				toolCallingMode: "text",
			});
			for (const event of textAttempt.events) stream.push(event);
			if (textAttempt.error) throw textAttempt.error;
			stream.end();
		} catch (error) {
			output.stopReason = "error";
			output.errorMessage = error instanceof Error ? error.message : String(error);
			stream.push({ type: "error", reason: "error", error: output });
			stream.end();
		}
	})();

	return stream;
};

export const streamSimpleQwenWrapper: StreamFunction<"qwen-wrapper", SimpleStreamOptions> = (
	model,
	context,
	options,
) => {
	return streamQwenWrapper(model as Model<"qwen-wrapper">, context, {
		...options,
		apiKey: options?.apiKey,
		toolCallingMode: (options as QwenWrapperOptions | undefined)?.toolCallingMode || "auto",
	});
};

async function collectAttempt(
	model: Model<"qwen-wrapper">,
	context: Context,
	options: QwenWrapperOptions,
): Promise<{ events: any[]; error?: Error }> {
	const events: any[] = [];
	try {
		for await (const event of streamOnce(model, context, options)) events.push(event);
		return { events };
	} catch (error) {
		return { events, error: error as Error };
	}
}

async function* streamOnce(
	model: Model<"qwen-wrapper">,
	context: Context,
	options: QwenWrapperOptions,
) {
	const payload = constructRequest(model, context, options);
	const partial = createPartialMessage(model);
	yield { type: "start", partial };

	let textIndex: number | undefined;
	let thinkingIndex: number | undefined;

	for await (const chunk of streamRequest(payload, options)) {
		if (chunk.result !== undefined && chunk.status) {
			if (textIndex === undefined) {
				partial.content.push({ type: "text", text: "" } satisfies TextContent);
				textIndex = partial.content.length - 1;
				yield { type: "text_start", contentIndex: textIndex, partial };
			}

			const block = partial.content[textIndex] as TextContent;
			block.text += String(chunk.result || "");
			yield {
				type: "text_delta",
				contentIndex: textIndex,
				delta: String(chunk.result || ""),
				partial,
			};

			if ((chunk.status || "").toLowerCase() === "completed") {
				const recovered = extractTextToolCalls(block.text, context.tools || []);
				yield { type: "text_end", contentIndex: textIndex, content: block.text, partial };
				for (const toolCall of recovered) {
					partial.content.push(toolCall);
					const ci = partial.content.length - 1;
					yield { type: "toolcall_start", contentIndex: ci, partial };
					yield {
						type: "toolcall_delta",
						contentIndex: ci,
						delta: JSON.stringify(toolCall.arguments),
						partial,
					};
					yield { type: "toolcall_end", contentIndex: ci, toolCall, partial };
				}
				partial.stopReason = recovered.length ? "toolUse" : "stop";
				yield { type: "done", reason: partial.stopReason, message: partial };
				return;
			}
			continue;
		}

		const choice = chunk.output?.choices?.[0];
		const message = choice?.message || choice?.delta || {};
		const finishReason = choice?.finish_reason || chunk.output?.finish_reason;

		const reasoningText =
			message.reasoning_content || message.reasoning || message.reasoning_text;
		if (reasoningText) {
			if (thinkingIndex === undefined) {
				partial.content.push({
					type: "thinking",
					thinking: "",
				} satisfies ThinkingContent);
				thinkingIndex = partial.content.length - 1;
				yield { type: "thinking_start", contentIndex: thinkingIndex, partial };
			}
			const block = partial.content[thinkingIndex] as ThinkingContent;
			block.thinking += String(reasoningText);
			yield {
				type: "thinking_delta",
				contentIndex: thinkingIndex,
				delta: String(reasoningText),
				partial,
			};
			yield {
				type: "thinking_end",
				contentIndex: thinkingIndex,
				content: block.thinking,
				partial,
			};
		}

		const contentText = extractMessageText(message.content);
		if (contentText) {
			if (textIndex === undefined) {
				partial.content.push({ type: "text", text: "" } satisfies TextContent);
				textIndex = partial.content.length - 1;
				yield { type: "text_start", contentIndex: textIndex, partial };
			}
			const block = partial.content[textIndex] as TextContent;
			block.text += contentText;
			yield { type: "text_delta", contentIndex: textIndex, delta: contentText, partial };
		}

		const rawToolCalls = message.tool_calls || [];
		for (const rawToolCall of rawToolCalls) {
			const toolCall = normalizeToolCall(rawToolCall);
			if (!toolCall) continue;
			partial.content.push(toolCall);
			const ci = partial.content.length - 1;
			yield { type: "toolcall_start", contentIndex: ci, partial };
			yield {
				type: "toolcall_delta",
				contentIndex: ci,
				delta: JSON.stringify(toolCall.arguments),
				partial,
			};
			yield { type: "toolcall_end", contentIndex: ci, toolCall, partial };
		}

		if (!finishReason) continue;

		const existingToolCalls = partial.content.filter((c: any) => c.type === "toolCall");
		if (textIndex !== undefined) {
			const block = partial.content[textIndex] as TextContent;
			yield { type: "text_end", contentIndex: textIndex, content: block.text, partial };
			if (!existingToolCalls.length) {
				const recovered = extractTextToolCalls(block.text, context.tools || []);
				for (const toolCall of recovered) {
					partial.content.push(toolCall);
					const ci = partial.content.length - 1;
					yield { type: "toolcall_start", contentIndex: ci, partial };
					yield {
						type: "toolcall_delta",
						contentIndex: ci,
						delta: JSON.stringify(toolCall.arguments),
						partial,
					};
					yield { type: "toolcall_end", contentIndex: ci, toolCall, partial };
				}
			}
		}

		partial.stopReason =
			finishReason === "tool_calls" || partial.content.some((c: any) => c.type === "toolCall")
				? "toolUse"
				: mapStopReason(finishReason);
		yield { type: "done", reason: partial.stopReason, message: partial };
		return;
	}

	yield { type: "done", reason: partial.stopReason, message: partial };
}

function createPartialMessage(model: Model<any>): AssistantMessage {
	return {
		role: "assistant",
		content: [],
		api: model.api,
		provider: model.provider,
		model: model.id,
		usage: {
			input: 0,
			output: 0,
			cacheRead: 0,
			cacheWrite: 0,
			totalTokens: 0,
			cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
		},
		stopReason: "stop",
		timestamp: Date.now(),
	};
}

function constructRequest(
	model: Model<"qwen-wrapper">,
	context: Context,
	options: QwenWrapperOptions,
): QwenRequestTemplate {
	const mode = resolveToolCallingMode(context.tools || [], options.toolCallingMode);
	const tools = context.tools || [];
	const apiTools = buildTools(tools);
	const template = cloneJson(
		options.requestTemplate || {
			token: "",
			apikey: "",
			type: "txt",
			modelId: model.id,
			appInfo: {
				temperature: 0.3,
				prompt: "(tools)",
			},
			variable: [{ name: "tools", value: "" }],
			data: { messages: [], stream: true },
		},
	);

	template.token = options.apiKey || template.token || "";
	template.apikey = options.appApiKey || template.apikey || "";
	template.modelId = model.id;
	template.variable = [
		{
			name: "tools",
			value: buildPrompt(context.systemPrompt || "", apiTools, mode),
		},
	];

	template.data ||= {};
	template.data.stream = true;
	template.data.messages = buildMessages(context.messages);
	delete template.data.tools;
	return template;
}

function buildPrompt(systemPrompt: string, apiTools: unknown[], mode: "text"): string {
	const shared = [
		"When repository inspection is needed, do not guess.",
		"If the exact path is unknown, search first.",
		"Before editing a file, read the relevant content first.",
		"If a tool call fails, adjust the next call instead of repeating it unchanged.",
	].join("\n");

	const toolsBlock = apiTools.map((tool) => JSON.stringify(tool, null, 2)).join("\n");
	return [
		systemPrompt,
		shared,
		[
			"If a tool is needed, output exactly one tool call block and nothing else.",
			'Use this format: <tool_call>{"name":"<tool-name>","arguments":{...}}</tool_call>',
			"Do not wrap the JSON in markdown fences.",
			"Use only tool names listed in <tools>.",
		].join("\n"),
		`<tools>\n${toolsBlock}\n</tools>`,
	].filter(Boolean).join("\n\n");
}

function buildTools(tools: Tool[]): unknown[] {
	return tools.map((tool) => ({
		type: "function",
		function: {
			name: tool.name,
			description: tool.description,
			parameters: tool.parameters,
		},
	}));
}

function buildMessages(messages: Message[]): unknown[] {
	return messages.map((message) => {
		if (message.role === "user") {
			return { role: "user", content: buildUserContent(message.content) };
		}
		if (message.role === "assistant") {
			const text = message.content
				.filter((block) => block.type === "text")
				.map((block: any) => block.text)
				.join("");
			const toolCalls = message.content
				.filter((block) => block.type === "toolCall")
				.map((block: any) => ({
					id: block.id,
					type: "function",
					function: {
						name: block.name,
						arguments: JSON.stringify(block.arguments),
					},
				}));
			return {
				role: "assistant",
				...(text ? { content: [{ type: "text", text }] } : {}),
				...(toolCalls.length ? { tool_calls: toolCalls } : {}),
			};
		}
		const toolMessage = message as ToolResultMessage;
		return {
			role: "tool",
			tool_call_id: toolMessage.toolCallId,
			content: toolMessage.content
				.filter((block) => block.type === "text")
				.map((block: any) => block.text)
				.join("\n"),
		};
	});
}

function buildUserContent(content: Message["content"]): unknown[] | string {
	if (typeof content === "string") return content;
	return content.map((block: any) =>
		block.type === "text"
			? { type: "text", text: block.text }
			: {
					type: "image",
					data: block.data,
					mime_type: block.mimeType,
				},
	);
}

function resolveToolCallingMode(tools: Tool[], requested = "auto"): "text" {
	if (!tools.length) return "text";
	void requested;
	return "text";
}

function normalizeToolCall(rawToolCall: any): ToolCall | null {
	const fn = rawToolCall?.function || {};
	const name = fn.name || rawToolCall?.name;
	if (!name) return null;
	return {
		type: "toolCall",
		id: rawToolCall.id || `call_${Math.random().toString(36).slice(2)}`,
		name,
		arguments: parseToolArguments(fn.arguments ?? rawToolCall.arguments),
	};
}

function parseToolArguments(raw: unknown): Record<string, unknown> {
	if (!raw) return {};
	if (typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
	if (typeof raw === "string") {
		try {
			const parsed = JSON.parse(normalizeToolCallText(raw));
			return typeof parsed === "object" && parsed && !Array.isArray(parsed) ? parsed : {};
		} catch {
			return {};
		}
	}
	return {};
}

function extractTextToolCalls(text: string, tools: Tool[]): ToolCall[] {
	const available = new Set(tools.map((tool) => tool.name));
	const candidates = extractJsonCandidates(normalizeToolCallText(text));
	const seen = new Set<string>();
	const extracted: ToolCall[] = [];

	for (const candidate of candidates) {
		const rawToolCalls: any[] = [];
		if (Array.isArray(candidate)) rawToolCalls.push(...candidate);
		else if (candidate?.tool_calls && Array.isArray(candidate.tool_calls)) rawToolCalls.push(...candidate.tool_calls);
		else if (candidate?.tool_call) rawToolCalls.push(candidate.tool_call);
		else rawToolCalls.push(candidate);

		for (const rawToolCall of rawToolCalls) {
			const fn = rawToolCall?.function || rawToolCall || {};
			const name = fn.name || rawToolCall?.name;
			if (!name || !available.has(name)) continue;
			const argumentsObject = parseToolArguments(fn.arguments ?? rawToolCall.arguments);
			const key = `${name}:${JSON.stringify(argumentsObject)}`;
			if (seen.has(key)) continue;
			seen.add(key);
			extracted.push({
				type: "toolCall",
				id: rawToolCall.id || `call_${Math.random().toString(36).slice(2)}`,
				name,
				arguments: argumentsObject,
			});
		}
	}

	return extracted;
}

function normalizeToolCallText(text: string): string {
	return text
		.replace(/\ufeff/g, "")
		.replace(/\u200b/g, "")
		.replace(/\u200c/g, "")
		.replace(/\u200d/g, "")
		.replace(/\u2060/g, "")
		.replace(/\r\n/g, "\n")
		.replace(/\r/g, "\n")
		.trim();
}

function extractJsonCandidates(text: string): any[] {
	const decoder = JSON;
	const candidates: any[] = [];
	for (let i = 0; i < text.length; i++) {
		if (text[i] !== "{" && text[i] !== "[") continue;
		try {
			candidates.push(decoder.parse(text.slice(i)));
		} catch {
			continue;
		}
	}
	return candidates;
}

function extractMessageText(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.map((item: any) => (typeof item === "string" ? item : item?.text || item?.value || ""))
		.join("");
}

async function* streamRequest(
	payload: QwenRequestTemplate,
	options: QwenWrapperOptions,
): AsyncGenerator<QwenChunk> {
	const response = await fetch(options.baseUrl || DEFAULT_BASE_URL, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${options.apiKey || ""}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify(payload),
		signal: options.signal,
	});

	if (!response.ok) {
		throw new Error(`Qwen wrapper request failed: ${response.status} ${response.statusText}`);
	}

	const reader = response.body?.getReader();
	if (!reader) return;

	const decoder = new TextDecoder();
	let buffer = "";
	while (true) {
		const { value, done } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		const lines = buffer.split("\n");
		buffer = lines.pop() || "";
		for (const rawLine of lines) {
			const line = rawLine.trim().replace(/^data:\s*/, "");
			if (!line || line === "[DONE]") continue;
			try {
				yield JSON.parse(line) as QwenChunk;
			} catch {
				// Ignore malformed lines from the wrapper stream.
			}
		}
	}
}

function mapStopReason(reason: unknown): StopReason {
	switch (reason) {
		case "tool_calls":
			return "toolUse";
		case "length":
			return "length";
		case "stop":
		case "end":
		default:
			return "stop";
	}
}

function cloneJson<T>(value: T): T {
	return JSON.parse(JSON.stringify(value));
}
