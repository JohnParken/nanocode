#!/usr/bin/env python3
"""lightcodemcp - minimal coding assistant with MCP tool dispatch"""

import glob as globlib, json, os, re, ssl, subprocess, urllib.request, urllib.error

LIGHTCODE_TOKEN = os.environ.get("LIGHTCODE_TOKEN", "")
LIGHTCODE_APIKEY = os.environ.get("LIGHTCODE_APIKEY", "")
API_URL = os.environ.get("API_URL", "")
MODEL = os.environ.get("MODEL", "lightapplication")
AGENT_ID = os.environ.get("LIGHTCODE_AGENT_ID", "")

# ANSI colors
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, YELLOW, RED, MAGENTA = (
    "\033[34m",
    "\033[36m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[35m",
)

LOG = os.environ.get("NANOCODE_LOG", "1") not in ("0", "false", "")
LOG_FILE = os.environ.get("NANOCODE_LOG_FILE", os.path.join(os.path.dirname(__file__), "lightcodemcp.log") if LOG else None)


def _log(msg):
    """Print log to stdout and append to file (ANSI codes stripped for file)."""
    if not LOG:
        return
    print(msg)
    if LOG_FILE:
        with open(LOG_FILE, "a") as f:
            f.write(re.sub(r"\033\[[0-9;]*m", "", msg) + "\n")

if os.environ.get("NANOCODE_INSECURE"):
    SSL_CONTEXT = ssl._create_unverified_context()
elif os.environ.get("NANOCODE_CA_BUNDLE"):
    SSL_CONTEXT = ssl.create_default_context(cafile=os.environ["NANOCODE_CA_BUNDLE"])
else:
    SSL_CONTEXT = ssl.create_default_context()


# --- Tool implementations ---


def read(args):
    lines = open(args["path"]).readlines()
    offset = args.get("offset", 0)
    limit = args.get("limit", len(lines))
    selected = lines[offset : offset + limit]
    return "".join(f"{offset + idx + 1:4}| {line}" for idx, line in enumerate(selected))


def write(args):
    with open(args["path"], "w") as f:
        f.write(args["content"])
    return "ok"


def edit(args):
    text = open(args["path"]).read()
    old, new = args["old"], args["new"]
    if old not in text:
        return "error: old_string not found"
    count = text.count(old)
    if not args.get("all") and count > 1:
        return f"error: old_string appears {count} times, must be unique (use all=true)"
    replacement = (
        text.replace(old, new) if args.get("all") else text.replace(old, new, 1)
    )
    with open(args["path"], "w") as f:
        f.write(replacement)
    return "ok"


def glob(args):
    pattern = (args.get("path", ".") + "/" + args["pat"]).replace("//", "/")
    files = globlib.glob(pattern, recursive=True)
    files = sorted(
        files,
        key=lambda f: os.path.getmtime(f) if os.path.isfile(f) else 0,
        reverse=True,
    )
    return "\n".join(files) or "none"


def grep(args):
    pattern = re.compile(args["pat"])
    hits = []
    for filepath in globlib.glob(args.get("path", ".") + "/**", recursive=True):
        try:
            for line_num, line in enumerate(open(filepath), 1):
                if pattern.search(line):
                    hits.append(f"{filepath}:{line_num}:{line.rstrip()}")
        except Exception:
            pass
    return "\n".join(hits[:50]) or "none"


def bash(args):
    proc = subprocess.Popen(
        args["cmd"], shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True
    )
    output_lines = []
    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                print(f"  {DIM}│ {line.rstrip()}{RESET}", flush=True)
                output_lines.append(line)
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        output_lines.append("\n(timed out after 30s)")
    return "".join(output_lines).strip() or "(empty)"


# --- Tool definitions: (description, schema, function) ---

TOOLS = {
    "read": (
        "Read file with line numbers (file path, not directory)",
        {"path": "string", "offset": "number?", "limit": "number?"},
        read,
    ),
    "write": (
        "Write content to file",
        {"path": "string", "content": "string"},
        write,
    ),
    "edit": (
        "Replace old with new in file (old must be unique unless all=true)",
        {"path": "string", "old": "string", "new": "string", "all": "boolean?"},
        edit,
    ),
    "glob": (
        "Find files by pattern, sorted by mtime",
        {"pat": "string", "path": "string?"},
        glob,
    ),
    "grep": (
        "Search files for regex pattern",
        {"pat": "string", "path": "string?"},
        grep,
    ),
    "bash": (
        "Run shell command",
        {"cmd": "string"},
        bash,
    ),
}


def run_tool(name, args):
    try:
        return TOOLS[name][2](args)
    except Exception as err:
        return f"error: {err}"


def make_schema():
    result = []
    for name, (description, params, _fn) in TOOLS.items():
        properties = {}
        required = []
        for param_name, param_type in params.items():
            is_optional = param_type.endswith("?")
            base_type = param_type.rstrip("?")
            properties[param_name] = {
                "type": "integer" if base_type == "number" else base_type
            }
            if not is_optional:
                required.append(param_name)
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
    return result


def handle_mcp_request(request):
    """Process a MCP JSON-RPC request, return JSON-RPC response.
    Supports: initialize, tools/list, tools/call."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lightcodemcp", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": make_schema()}}
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
        result_text = run_tool(name, arguments)
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "content": [{"type": "text", "text": result_text}],
                "isError": result_text.startswith("error:"),
            },
        }
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


_CALL_ID = 0
def mcp_call(method, params=None):
    """Send a JSON-RPC request to the MCP server, return result dict."""
    global _CALL_ID
    _CALL_ID += 1
    request = {"jsonrpc": "2.0", "id": _CALL_ID, "method": method, "params": params or {}}
    response = handle_mcp_request(request)
    if "error" in response:
        raise RuntimeError(f"MCP {response['error']['code']}: {response['error']['message']}")
    return response["result"]


def build_tool_prompt(system_prompt):
    """Assemble the full system prompt with text-mode tool protocol."""
    schemas = make_schema()
    tools_json = "\n".join(json.dumps(s, ensure_ascii=False) for s in schemas)
    return f"""{system_prompt}

# Tool Use Protocol

You have access to the following tools. To use a tool, emit a <tool_call> block with valid JSON inside.

# Available Tools

<tools>
{tools_json}
</tools>

# Tool Call Rules
- To call a tool, output EXACTLY: <tool_call>{{"name":"<name>","arguments":{{...}}}}</tool_call>
- One <tool_call> block per tool call. For multiple tools, emit multiple blocks.
- The JSON inside must use double quotes and be valid.
- "arguments" must be a JSON object, not a string.
- Only call tools listed above. Do not invent new tools.
"""


def extract_tool_calls(text):
    """Parse <tool_call> blocks from model response text.
    Returns list of (tool_name, tool_args, tool_call_id) tuples."""
    pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
    matches = pattern.findall(text)
    results = []
    seen = set()
    for raw_json in matches:
        raw_json = raw_json.strip()
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
        raw_json = re.sub(r"\s*```$", "", raw_json)
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        name = parsed.get("name", "")
        arguments = parsed.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        if name not in TOOLS:
            continue
        call_id = parsed.get("id", f"call_{name}_{len(seen) + 1}")
        if call_id in seen:
            continue
        seen.add(call_id)
        results.append((name, arguments, call_id))
    return results


def call_api(messages, system_prompt):
    """Send request to wrapper API and return assembled text response."""
    body = {
        "token": LIGHTCODE_TOKEN,
        "apikey": LIGHTCODE_APIKEY,
        "type": "txt",
        "modelId": MODEL,
        "appInfo": {
            "agent_id": AGENT_ID,
            "sensitive_judge": False,
            "safe_model_judge": False,
            "max_new_tokens": 81920,
            "temperature": 0.3,
            "name": "Chat-Medium",
            "prompt": "(static_memory)\n(tools)",
        },
        "variable": [
            {"name": "static_memory", "value": ""},
            {"name": "tools", "value": ""},
        ],
        "data": {
            "messages": messages,
            "stream": True,
        },
    }
    raw_body = json.dumps(body)

    _log(f"\n{DIM}{'─'*50}{RESET}\n{YELLOW}[log] >>> REQUEST to {API_URL}{RESET}\n{DIM}{raw_body[:2000]}{RESET}")

    request = urllib.request.Request(
        API_URL,
        data=raw_body.encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(request, context=SSL_CONTEXT)
        content_type = response.headers.get("Content-Type", "")

        if "text/event-stream" in content_type or "stream" in content_type:
            full_text = ""
            for line in response:
                line = line.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    # Wrapper format: accumulate result
                    if "result" in chunk:
                        full_text += chunk.get("result", "")
                    # OpenAI-like format: output.choices
                    elif "output" in chunk:
                        choices = chunk["output"].get("choices", [])
                        if choices and "message" in choices[0]:
                            delta = choices[0]["message"].get("content", "")
                            full_text += delta
                    # OpenAI-like format: choices with delta
                    elif "choices" in chunk:
                        choices = chunk.get("choices", [])
                        if choices and "delta" in choices[0]:
                            delta = choices[0]["delta"].get("content", "")
                            full_text += delta
                        elif choices and "message" in choices[0]:
                            full_text = choices[0]["message"].get("content", "")
                    # Error detection
                    if chunk.get("resCode") and chunk["resCode"] != 200:
                        raise RuntimeError(f"API error {chunk['resCode']}: {chunk.get('resMessage', '')}")
            _log(f"{YELLOW}[log] <<< STREAM RESPONSE ({len(full_text)} chars){RESET}\n{DIM}{full_text[:2000]}{RESET}")
            return full_text
        else:
            raw_response = response.read()
            _log(f"{YELLOW}[log] <<< RESPONSE (HTTP {response.status}){RESET}\n{DIM}{raw_response.decode()[:2000]}{RESET}")
            result = json.loads(raw_response)
            if "result" in result:
                return result["result"]
            if "output" in result:
                choices = result["output"].get("choices", [])
                if choices and "message" in choices[0]:
                    return choices[0]["message"].get("content", "")
            if "choices" in result:
                choices = result.get("choices", [])
                if choices and "message" in choices[0]:
                    return choices[0]["message"].get("content", "")
            return str(result)
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()[:500]
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body_err}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason} (URL: {API_URL})") from None
    except OSError as e:
        raise RuntimeError(f"Connection error: {e}") from None


def separator():
    return f"{DIM}{'─' * min(os.get_terminal_size().columns, 80)}{RESET}"


def render_markdown(text):
    return re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)


def _msg_brief(msg):
    """Return a compact one-line summary of a message for logging."""
    content = msg["content"]
    role = msg["role"]
    if role == "system":
        return "system: <tool prompt>"
    if role == "tool":
        tc_id = msg.get("tool_call_id", "?")
        if isinstance(content, list) and content:
            preview = content[0].get("text", str(content))[:80]
        else:
            preview = str(content)[:80]
        return f"tool({tc_id}): \"{preview}\""
    if isinstance(content, str):
        n_tc = content.count("<tool_call>")
        preview = content.replace("\n", " ")[:60]
        extra = f" + {n_tc} tc" if n_tc else ""
        return f"{role}: \"{preview}{'…' if len(content) > 60 else ''}\"{extra}"
    if isinstance(content, list):
        counts = {}
        for b in content:
            t = b.get("type", "?")
            counts[t] = counts.get(t, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        return f"{role}: {' + '.join(parts)}"
    return f"{role}: ?"


def main():
    print(f"{BOLD}lightcodemcp{RESET} | {DIM}{MODEL} (MCP dispatch){RESET} | {os.getcwd()}{RESET}\n")
    system_prompt = f"Concise coding assistant. cwd: {os.getcwd()}"
    messages = [{"role": "system", "content": build_tool_prompt(system_prompt)}]

    while True:
        try:
            print(separator())
            user_input = input(f"{BOLD}{BLUE}❯{RESET} ").strip()
            print(separator())
            if not user_input:
                continue
            if user_input in ("/q", "exit"):
                break
            if user_input == "/c":
                messages = [{"role": "system", "content": build_tool_prompt(system_prompt)}]
                print(f"{GREEN}⏺ Cleared conversation{RESET}")
                continue

            messages.append({"role": "user", "content": [{"type": "text", "text": user_input}]})

            # agentic loop
            iter_no = 0
            while True:
                iter_no += 1
                _log(f"\n{DIM}{'─'*50}{RESET}\n{MAGENTA}[log] iter {iter_no} | {len(messages)} msg → {MODEL}{RESET}\n" +
                     "\n".join(f"  {DIM}{_msg_brief(m)}{RESET}" for m in messages) +
                     f"\n  {DIM}tools: {', '.join(TOOLS)}{RESET}")

                full_text = call_api(messages, system_prompt)
                tool_calls = extract_tool_calls(full_text)

                if LOG:
                    _log(f"  {GREEN}← response: {len(full_text)} chars | {len(tool_calls)} tool calls{RESET}")

                # Display text with <tool_call> blocks stripped
                display_text = re.sub(r"<tool_call>.*?</tool_call>", "", full_text, flags=re.DOTALL).strip()
                if display_text:
                    print(f"\n{CYAN}⏺{RESET} {render_markdown(display_text)}")

                tool_results = []
                for tool_name, tool_args, tool_call_id in tool_calls:
                    arg_preview = str(list(tool_args.values())[0] if tool_args else "?")[:50]
                    print(
                        f"\n{GREEN}⏺ {tool_name.capitalize()}{RESET}({DIM}{arg_preview}{RESET})"
                    )

                    _log(f"  {DIM}[log] full args: {json.dumps(tool_args, ensure_ascii=False)}{RESET}")

                    mcp_result = mcp_call("tools/call", {"name": tool_name, "arguments": tool_args})
                    result_text = mcp_result["content"][0]["text"]
                    is_error = mcp_result.get("isError", False)

                    result_lines = result_text.split("\n")
                    preview = result_lines[0][:60]
                    if len(result_lines) > 1:
                        preview += f" ... +{len(result_lines) - 1} lines"
                    elif len(result_lines[0]) > 60:
                        preview += "..."
                    if is_error:
                        print(f"  {RED}⎿  {preview}{RESET}")
                    else:
                        print(f"  {DIM}⎿  {preview}{RESET}")

                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": mcp_result["content"],
                        }
                    )

                messages.append({"role": "assistant", "content": full_text})

                if not tool_results:
                    _log(f"  {DIM}[log] no tool calls → agent loop done{RESET}")
                    break

                messages.extend(tool_results)
                _log(f"  {DIM}[log] {len(tool_results)} tool result(s) appended, looping again{RESET}")

            print()

        except (KeyboardInterrupt, EOFError):
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")


if __name__ == "__main__":
    main()
