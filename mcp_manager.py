import os
import sys
import json
import asyncio
import shutil
import threading
from typing import Dict, Any, List, Optional

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False


class MCPManager:
    """
    Model Context Protocol (MCP) Manager for Ultron.
    Supports connecting to external stdio MCP servers and provides
    zero-key built-in MCP tools (Live Web Search, Web Fetch, SQLite).
    """

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_servers.json")
        self.config_path = config_path
        self.servers: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """Loads MCP servers configuration from mcp_servers.json."""
        if not os.path.exists(self.config_path):
            default_config = {
                "mcpServers": {
                    "fetch": {
                        "command": sys.executable,
                        "args": ["-m", "mcp_server_fetch"],
                        "description": "Fetch and convert web pages to clean markdown"
                    }
                }
            }
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(default_config, f, indent=2)
            except Exception as e:
                print(f"[MCP] Could not write default config: {e}")

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.servers = data.get("mcpServers", {})
                return self.servers
        except Exception as e:
            print(f"[MCP] Config load error: {e}")
            self.servers = {}
            return {}

    def _resolve_executable(self, cmd: str) -> str:
        """Resolves relative command names like python, npx to full paths on Windows."""
        if cmd.lower() in ("python", "python.exe"):
            return sys.executable
        resolved = shutil.which(cmd)
        if resolved:
            return resolved
        if cmd.lower() == "npx" and sys.platform == "win32":
            resolved = shutil.which("npx.cmd")
            if resolved:
                return resolved
        return cmd

    async def list_tools_async(self, server_name: str) -> List[Dict[str, Any]]:
        """Lists available tools from a registered MCP server."""
        if not MCP_AVAILABLE:
            raise RuntimeError("MCP Python SDK is not installed.")

        cfg = self.servers.get(server_name)
        if not cfg:
            raise ValueError(f"MCP server '{server_name}' not configured.")

        cmd = self._resolve_executable(cfg.get("command", ""))
        args = cfg.get("args", [])
        env = cfg.get("env", None)

        params = StdioServerParameters(command=cmd, args=args, env=env)
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else getattr(tool, "input_schema", {})
                    }
                    for tool in tools_result.tools
                ]

    def list_tools(self, server_name: str) -> List[Dict[str, Any]]:
        """Synchronous wrapper for list_tools_async."""
        return asyncio.run(self.list_tools_async(server_name))

    async def call_tool_async(self, server_name: str, tool_name: str, arguments: Dict[str, Any] = None) -> Any:
        """Executes a tool on a registered MCP server using the MCP JSON-RPC protocol."""
        if not MCP_AVAILABLE:
            raise RuntimeError("MCP Python SDK is not installed.")

        cfg = self.servers.get(server_name)
        if not cfg:
            raise ValueError(f"MCP server '{server_name}' not configured in mcp_servers.json.")

        cmd = self._resolve_executable(cfg.get("command", ""))
        args = cfg.get("args", [])
        env = cfg.get("env", None)
        arguments = arguments or {}

        params = StdioServerParameters(command=cmd, args=args, env=env)
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                # Parse content items
                output_parts = []
                for item in getattr(result, "content", []):
                    if hasattr(item, "text"):
                        output_parts.append(item.text)
                    elif hasattr(item, "data"):
                        output_parts.append(str(item.data))
                return "\n".join(output_parts) if output_parts else str(result)

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any] = None) -> Any:
        """Synchronous wrapper for call_tool_async."""
        return asyncio.run(self.call_tool_async(server_name, tool_name, arguments))

    # -------------------------------------------------------------
    # ZERO-KEY BUILT-IN MCP CAPABILITIES
    # -------------------------------------------------------------
    def web_search(self, query: str, max_results: int = 5) -> str:
        """
        Live web search using DuckDuckGo (Zero API keys required).
        Returns top results with titles, snippets, and URLs.
        """
        query = (query or "").strip()
        if not query:
            return "No search query provided."

        if not DDGS_AVAILABLE:
            return "Web search dependency is not installed. Run 'pip install ddgs'."

        results = []
        try:
            results = list(DDGS().text(query, max_results=max_results))
        except Exception:
            pass

        if not results:
            try:
                results = list(DDGS().text(query, max_results=max_results, backend="html"))
            except Exception as e:
                return f"Web search error: {e}"

        if not results:
            return f"No results found for '{query}'."

        formatted = [f"Search results for '{query}':"]
        for idx, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            snippet = r.get("body", "")
            link = r.get("href", "")
            formatted.append(f"{idx}. {title}\n   {snippet}\n   URL: {link}")

        return "\n\n".join(formatted)

    def web_fetch(self, url: str) -> str:
        """
        Fetches web page content and extracts readable markdown text
        using MCP Fetch or fallback article extractor (Zero keys required).
        """
        url = (url or "").strip()
        if not url:
            return "No URL provided."

        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url

        # Try MCP Fetch server first if configured
        if "fetch" in self.servers and MCP_AVAILABLE:
            try:
                return self.call_tool("fetch", "fetch", {"url": url})
            except Exception as mcp_err:
                print(f"[MCP] stdio fetch failed ({mcp_err}), using direct fallback.")

        # Fallback direct fetcher using readabilipy/markdownify/requests
        try:
            import requests
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = requests.get(url, headers=headers, timeout=12)
            resp.raise_for_status()

            try:
                import markdownify
                text = markdownify.markdownify(resp.text, heading_style="ATX")
                # Clean up excessive newlines
                text = "\n".join(line for line in text.splitlines() if line.strip())
                return text[:3000] if len(text) > 3000 else text
            except ImportError:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                return text[:3000] if len(text) > 3000 else text
        except Exception as e:
            return f"Failed to fetch content from {url}: {e}"

    def query_sqlite(self, db_path: str, query: str) -> str:
        """
        Executes a read-only query on a local SQLite database (Zero keys required).
        """
        import sqlite3
        if not os.path.exists(db_path):
            return f"Database file not found: {db_path}"

        query_clean = query.strip()
        # Enforce read-only safety for general voice/assistant queries
        if not query_clean.lower().startswith(("select", "pragma", "explain")):
            return "For safety, only SELECT and read-only queries are supported via this tool."

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(query_clean)
            rows = cursor.fetchmany(50)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            conn.close()

            if not rows:
                return "Query executed successfully. 0 rows returned."

            output = [" | ".join(columns), "-" * (len(" | ".join(columns)) + 4)]
            for r in rows:
                output.append(" | ".join(str(val) for val in r))
            return "\n".join(output)
        except Exception as e:
            return f"SQLite error: {e}"

    # -------------------------------------------------------------
    # NATURAL VOICE COMMAND DISPATCHER
    # -------------------------------------------------------------
    def handle_command(self, command: str) -> Optional[str]:
        """
        Dispatches voice or text commands for web search, web fetch, or MCP tools.
        """
        cmd = (command or "").strip()
        cmd_lower = cmd.lower()

        # 1. Live Web Search Commands
        search_triggers = (
            "search the web for",
            "search web for",
            "search online for",
            "web search for",
            "web search",
            "google for",
            "google search",
            "search for"
        )
        for trigger in search_triggers:
            if cmd_lower.startswith(trigger):
                query = cmd[len(trigger):].strip()
                if query:
                    return self.web_search(query)

        # 2. Web Page Fetch Commands
        fetch_triggers = (
            "fetch website",
            "fetch url",
            "read website",
            "read url",
            "read page",
            "fetch page"
        )
        for trigger in fetch_triggers:
            if cmd_lower.startswith(trigger):
                target_url = cmd[len(trigger):].strip()
                if target_url:
                    return self.web_fetch(target_url)

        # 3. Direct MCP tool invocation via voice: "mcp call <server> <tool> <json_args>"
        if cmd_lower.startswith("mcp call "):
            parts = cmd[9:].strip().split(maxsplit=2)
            if len(parts) >= 2:
                server = parts[0]
                tool = parts[1]
                args = {}
                if len(parts) == 3:
                    try:
                        args = json.loads(parts[2])
                    except Exception:
                        pass
                try:
                    return self.call_tool(server, tool, args)
                except Exception as e:
                    return f"MCP execution error: {e}"

        return None


# Global singleton instance
mcp_manager = MCPManager()
