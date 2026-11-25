"""MCP server: the agent's Odoo tools for any MCP client (Claude Desktop, Cursor, IDEs...).

The tools, their argument validation and the read-only guarantee are the same ones the
/agent endpoint uses; only the transport changes. The client brings its own model, so no
LLM key is needed here (only for the procedures search if it uses Gemini embeddings).

    python -m app.mcp_server                 # stdio, for desktop clients
    python -m app.mcp_server --http 8001     # streamable HTTP on /mcp
"""

import argparse
import inspect
import logging
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.agent.loop import _run_tool
from app.agent.tools import Tool, build_tools
from app.config import Settings, get_settings

INSTRUCTIONS = (
    "Read-only access to an Odoo database of a food distribution company: products, stock "
    "per location, lots and expiry dates, pending deliveries, overdue customer invoices, "
    "and a search over the warehouse procedures. Nothing can be modified through these tools."
)


def _as_mcp_function(tool: Tool):
    """A function whose signature mirrors the tool's Pydantic arguments.

    The MCP SDK builds the JSON schema from the signature, so the field descriptions and
    limits (ge/le) end up in what the client's model sees. The call itself goes through
    the agent's _run_tool: same validation, same error format, same truncation.
    """

    def call(**kwargs) -> str:
        return _run_tool(tool, kwargs)

    params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[field.annotation, field],
            default=inspect.Parameter.empty if field.is_required() else field.default,
        )
        for name, field in tool.args.model_fields.items()
    ]
    call.__signature__ = inspect.Signature(params, return_annotation=str)
    call.__name__ = tool.name
    return call


def build_server(settings: Settings | None = None, tools: dict[str, Tool] | None = None) -> MCPServer:
    if tools is None:
        from app.main import _api_key, build_retriever
        from app.odoo.client import build_odoo

        settings = settings or get_settings()
        retriever = build_retriever(settings, _api_key(settings))
        tools = build_tools(build_odoo(settings), retriever)

    server = MCPServer("odoo-assistant", instructions=INSTRUCTIONS)
    for tool in tools.values():
        server.add_tool(
            _as_mcp_function(tool),
            name=tool.name,
            description=tool.description,
            annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
            structured_output=False,
        )
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP instead of stdio")
    args = parser.parse_args()
    # stdout is the protocol channel in stdio mode: logs must go to stderr.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    server = build_server()
    if args.http:
        server.run("streamable-http", host="127.0.0.1", port=args.http)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()


