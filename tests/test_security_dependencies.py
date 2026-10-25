"""Offline dependency policy and MCP compatibility checks."""

import asyncio
import importlib.util
import json
from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_manifests_exclude_vulnerable_framework_versions():
    poetry = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["poetry"][
        "dependencies"
    ]
    assert poetry["python"] == "^3.12"
    assert poetry["fastmcp"] == ">=3.4.10,<4.0.0"
    requirements = {
        r.name.lower(): r
        for raw in (ROOT / "requirements.txt").read_text().splitlines()
        if (line := raw.strip()) and not line.startswith("#")
        for r in [Requirement(line)]
    }
    assert "3.4.10" in requirements["fastmcp"].specifier
    assert "3.1.0" not in requirements["fastmcp"].specifier
    assert "2.14.0" not in requirements["pyjwt"].specifier
    assert "2.15.0" in requirements["pyjwt"].specifier
    assert "2.14.0" not in SpecifierSet(poetry["pyjwt"])


def test_isolated_mcp_tools_and_graphql(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for name in list(__import__("os").environ):
        if name.startswith("WIKIJS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("WIKIJS_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("WIKIJS_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("WIKIJS_MCP_DB", str(tmp_path / "mappings.db"))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "test.log"))
    spec = importlib.util.spec_from_file_location(
        "isolated_wiki_server", ROOT / "src/wiki_mcp_server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)

    async def exercise():
        import httpx
        from fastmcp import Client

        async def fake_wiki(request):
            assert request.url.host == "127.0.0.1"
            assert request.url.path == "/graphql"
            assert request.headers["authorization"] == "Bearer synthetic-test-token"
            assert json.loads(request.content)["query"] == "query { fixture }"
            return httpx.Response(200, json={"data": {"fixture": "ok"}})

        await server.wikijs.client.aclose()
        server.wikijs.client = httpx.AsyncClient(
            transport=httpx.MockTransport(fake_wiki)
        )
        try:
            assert await server.wikijs.authenticate()
            assert await server.wikijs.graphql_request("query { fixture }") == {
                "data": {"fixture": "ok"}
            }
            async with Client(server.mcp) as client:
                tools = await client.list_tools()
                assert len(tools) == 21
                assert "wikijs_get_page" in {tool.name for tool in tools}
                result = await client.call_tool("wikijs_repository_context", {})
                assert not result.is_error
        finally:
            await server.wikijs.client.aclose()
            server.engine.dispose()

    asyncio.run(exercise())
