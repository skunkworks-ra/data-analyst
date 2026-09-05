#!/usr/bin/env python3
"""Free (no-LLM) smoke test: speak MCP over stdio to the ms-inspect server
directly and confirm it starts, advertises tools, and answers a real call.

Proves the plugin's server process works without spending any Anthropic
tokens. Does not prove the skill loads or that Claude picks the right tool —
that part needs the paid LLM smoke test.
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> int:
    ms_path = sys.argv[1] if len(sys.argv) > 1 else None

    params = StdioServerParameters(
        command="bash",
        args=[os.path.join(REPO_ROOT, "bin", "serve.sh")],
        env=dict(os.environ, RADIO_MCP_TRANSPORT="stdio"),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = (await session.list_tools()).tools
            names = {t.name for t in tools}
            print(f"advertised {len(names)} tools")
            required = {"ms_observation_info", "ms_field_list", "ms_antenna_list"}
            missing = required - names
            if missing:
                print(f"FAIL: missing expected tools: {missing}")
                return 1

            if ms_path:
                result = await session.call_tool(
                    "ms_observation_info", {"params": {"ms_path": ms_path}}
                )
                text = result.content[0].text if result.content else ""
                print(f"ms_observation_info -> {text[:500]}")
                if result.isError:
                    print("FAIL: tool call returned an error")
                    return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
