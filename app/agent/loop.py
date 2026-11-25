"""A plain tool-calling loop, no framework: model -> tool calls -> results -> model."""

import json
import logging
import time
from datetime import date

from pydantic import ValidationError

from app.agent.tools import Tool

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the assistant of a food distribution company that runs Odoo \
(warehouse and customer invoices). Today is {today}.
- Use the tools to get real data. Never invent lots, quantities or dates.
- For "how do I / what is the procedure" questions, use search_procedures.
- Combine data and procedures when useful (e.g. expired lots -> what to do with them).
- Answer briefly, with lists or small tables. Mention lot numbers and quantities.
- You cannot modify Odoo. If an action is needed, say what the user should do.
- Answer in the language of the question."""

MAX_RESULT_CHARS = 4000


def _run_tool(tool: Tool, arguments: dict) -> str:
    try:
        args = tool.args.model_validate(arguments)
        result = tool.fn(**args.model_dump())
    except ValidationError as e:
        # Sent back to the model so it can fix its own call.
        return json.dumps({"error": "invalid arguments", "details": e.errors(include_url=False)})
    except Exception as e:  # an Odoo/network error must not kill the conversation
        log.exception("tool %s failed", tool.name)
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '... (truncated)'
    return text


def run_agent(question: str, llm, tools: dict[str, Tool], max_steps: int = 5,
              history: list[dict] | None = None) -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(today=date.today().isoformat())}]
    messages += history or []
    messages.append({"role": "user", "content": question})
    schemas = [t.schema() for t in tools.values()]
    steps = []
    start = time.perf_counter()

    for _ in range(max_steps):
        result = llm.chat(messages, tools=schemas, operation="agent")
        if not result.tool_calls:
            return {"answer": (result.content or "").strip(), "steps": steps,
                    "elapsed_ms": int((time.perf_counter() - start) * 1000)}
        messages.append(result.assistant_message())
        for call in result.tool_calls:
            tool = tools.get(call.name)
            output = (_run_tool(tool, call.arguments) if tool
                      else json.dumps({"error": f"unknown tool {call.name}"}))
            steps.append({"tool": call.name, "arguments": call.arguments, "output": output[:500]})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

    # Out of steps: ask for a final answer with what it has.
    messages.append({"role": "user", "content": "Answer now with the information you have."})
    result = llm.chat(messages, operation="agent")
    return {"answer": (result.content or "").strip(), "steps": steps, "truncated": True,
            "elapsed_ms": int((time.perf_counter() - start) * 1000)}
