"""Agent-facing CLI for HomeTrove.

homeTrove-cli forwards commands to the HomeTrove REST API. It is designed to
be consumed by automation / LLM agents rather than human operators:

    export HOMETROVE_CLI_HOST=http://127.0.0.1:8080
    export HOMETROVE_CLI_API_KEY=changeme

    hometrove-cli endpoints
    hometrove-cli describe search
    hometrove-cli search "sunset beach" --limit 5
    hometrove-cli upload ./photo.jpg --plugins thumbnail,exif
    hometrove-cli test-plugin basic.info ./photo.jpg

All responses are JSON (or markdown URLs for search/upload) so agents can
parse them reliably.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import http.cookiejar
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Endpoint map: command name -> HTTP metadata + parameter schema.
# This is the single source of truth for the generic ``call`` command and
# the ``describe`` / ``endpoints`` helpers.
# ---------------------------------------------------------------------------

ENDPOINTS: dict[str, dict[str, Any]] = {
    "health": {
        "method": "GET",
        "path": "/api/health",
        "params": {},
        "help": "server health check",
    },
    "assets": {
        "method": "GET",
        "path": "/api/assets",
        "params": {
            "media_type": {"type": "str", "help": "filter by image|video|other"},
            "cursor": {"type": "int", "help": "pagination cursor"},
            "limit": {"type": "int", "default": 60, "help": "page size (1-500)"},
            "tag": {"type": "str", "help": "filter by tag facet"},
            "category": {"type": "str", "help": "filter by category facet"},
            "person_id": {"type": "int", "help": "filter by person id"},
            "favorite": {"type": "bool", "help": "filter by favorite flag"},
            "taken_after": {"type": "int", "help": "taken_at >= epoch"},
            "taken_before": {"type": "int", "help": "taken_at < epoch"},
            "place": {"type": "str", "help": "place grid 'lat,lon'"},
        },
        "help": "list library assets with filters",
    },
    "asset": {
        "method": "GET",
        "path": "/api/assets/{asset_id}",
        "params": {
            "asset_id": {"type": "int", "required": True, "path": True, "help": "asset id"},
        },
        "help": "get a single asset detail",
    },
    "search": {
        "method": "GET",
        "path": "/api/search",
        "params": {
            "q": {"type": "str", "required": True, "help": "semantic / keyword query"},
            "limit": {"type": "int", "default": 40, "help": "max results (1-100)"},
        },
        "help": "hybrid semantic search",
    },
    "plugins": {
        "method": "GET",
        "path": "/api/plugins",
        "params": {},
        "help": "list installed plugins",
    },
    "plugin": {
        "method": "GET",
        "path": "/api/plugins/{plugin_id}",
        "params": {
            "plugin_id": {"type": "str", "required": True, "path": True, "help": "plugin id"},
        },
        "help": "get plugin details",
    },
    "persons": {
        "method": "GET",
        "path": "/api/persons",
        "params": {
            "include_assets": {"type": "bool", "help": "include asset ids per person"},
        },
        "help": "list persons",
    },
    "folders": {
        "method": "GET",
        "path": "/api/folders",
        "params": {},
        "help": "list media root folders",
    },
    "jobs": {
        "method": "GET",
        "path": "/api/jobs",
        "params": {},
        "help": "list worker jobs",
    },
}


# ---------------------------------------------------------------------------
# Low-level HTTP client
# ---------------------------------------------------------------------------

class CLIError(Exception):
    pass


class AgentClient:
    """Thin HTTP client that talks to one HomeTrove API host."""

    def __init__(self, host: str, api_key: str | None) -> None:
        self.host = host.rstrip("/")
        self.api_key = api_key
        self._cookie_jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookie_jar)
        )
        self._opener = opener

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "hometrove-cli/0.1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        url = urljoin(self.host + "/", path.lstrip("/"))
        if query:
            qs = urlencode({k: v for k, v in query.items() if v is not None})
            if qs:
                url = f"{url}?{qs}"
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers=self._headers(content_type=content_type),
        )
        try:
            with self._opener.open(req, timeout=120) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            data = exc.read()
            text = data.decode("utf-8", errors="replace")
            try:
                detail = json.loads(text)
            except json.JSONDecodeError:
                detail = text
            raise CLIError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise CLIError(f"request failed: {exc}") from exc
        if not data:
            return {}
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CLIError(f"invalid JSON from server: {exc}") from exc

    def upload_file(
        self,
        file_path: Path,
        *,
        plugins: list[str] | None = None,
        encrypted: bool = True,
    ) -> dict[str, Any]:
        """Upload a file using the chunked upload API.

        For simplicity the CLI uploads the file in a single chunk. The server
        supports arbitrary chunk sizes up to the configured limit, so a
        one-shot upload works for all files the agent is likely to handle.

        By default ``encrypted=True`` so the file is written into the vault
        when vault mode is enabled on the server.
        """
        size = file_path.stat().st_size
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            payload = f.read()
        sha.update(payload)
        content_hash = sha.hexdigest()
        filename = file_path.name

        # 1. create session
        session = self.request(
            "POST",
            "/api/uploads",
            body=json.dumps(
                {
                    "filename": filename,
                    "size": size,
                    "content_hash": content_hash,
                    "encrypted": encrypted,
                }
            ).encode("utf-8"),
            content_type="application/json",
        )
        upload_id = session["upload_id"]
        chunk_size = session["chunk_size"]

        # 2. upload chunks (single chunk if file fits)
        if size <= chunk_size:
            self._put_chunk(upload_id, 0, payload)
        else:
            for idx, start in enumerate(range(0, size, chunk_size)):
                self._put_chunk(upload_id, idx, payload[start:start + chunk_size])

        # 3. finalize
        chunk_count = (size + chunk_size - 1) // chunk_size if size else 1
        self.request(
            "POST",
            f"/api/uploads/{upload_id}/complete",
            body=json.dumps({"chunk_indices": list(range(chunk_count))}).encode("utf-8"),
            content_type="application/json",
        )

        # 4. ingest
        ingest_query: dict[str, Any] = {}
        if plugins:
            ingest_query["plugin_ids"] = plugins
        result = self.request(
            "POST",
            f"/api/uploads/{upload_id}/ingest",
            query=ingest_query,
        )
        return result

    def _put_chunk(self, upload_id: str, idx: int, data: bytes) -> None:
        boundary = "----hometrove-cli-boundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="chunk.bin"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1") + data + f"\r\n--{boundary}--\r\n".encode("latin-1")
        self.request(
            "PUT",
            f"/api/uploads/{upload_id}/chunks/{idx}",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def _coerce(value: str, typ: str) -> Any:
    if typ == "int":
        return int(value)
    if typ == "bool":
        return value.lower() in ("true", "1", "yes", "on")
    return value


def _parse_param(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"param must be key=value, got {raw!r}")
    key, value = raw.split("=", 1)
    return key.strip(), value.strip()


def _build_call_args(name: str, params: list[tuple[str, str]]) -> tuple[str, dict[str, Any]]:
    spec = ENDPOINTS[name]
    path = spec["path"]
    query: dict[str, Any] = {}
    for key, value in params:
        pmeta = spec.get("params", {}).get(key)
        if pmeta is None:
            raise CLIError(f"unknown parameter {key!r} for command {name!r}")
        coerced = _coerce(value, pmeta["type"])
        if pmeta.get("path"):
            path = path.replace("{" + key + "}", str(coerced))
        else:
            query[key] = coerced
    return path, query


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_endpoints(client: AgentClient, args: argparse.Namespace) -> int:
    for name, spec in ENDPOINTS.items():
        print(f"{name:20s} {spec['method']:6s} {spec['path']:<30s} # {spec['help']}")
    # Dedicated subcommands that do not go through the generic ``call`` path.
    print(f"{'upload':20s} {'POST':6s} {'/api/uploads (chunked)':<30s} # upload a file and ingest it")
    print(f"{'test-plugin':20s} {'POST':6s} {'/api/plugins/{plugin_id}/test':<30s} # run a plugin against a file")
    return 0


def cmd_describe(client: AgentClient, args: argparse.Namespace) -> int:
    spec = ENDPOINTS.get(args.command)
    if spec is None:
        print(f"unknown command: {args.command}", file=sys.stderr)
        return 2
    print(f"{args.command}: {spec['help']}")
    print(f"  {spec['method']} {spec['path']}")
    if not spec.get("params"):
        print("  (no parameters)")
        return 0
    print("  parameters:")
    for pname, pmeta in spec["params"].items():
        flags = []
        if pmeta.get("required"):
            flags.append("required")
        if "default" in pmeta:
            flags.append(f"default={pmeta['default']}")
        if pmeta.get("path"):
            flags.append("path")
        print(f"    {pname} ({pmeta['type']}) {', '.join(flags)}")
        if pmeta.get("help"):
            print(f"      {pmeta['help']}")
    return 0


def cmd_call(client: AgentClient, args: argparse.Namespace) -> int:
    path, query = _build_call_args(args.command, args.params)
    result = client.request(ENDPOINTS[args.command]["method"], path, query=query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_upload(client: AgentClient, args: argparse.Namespace) -> int:
    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"file not found: {file_path}", file=sys.stderr)
        return 2
    plugins = None
    if args.plugins:
        plugins = [p.strip() for p in args.plugins.split(",") if p.strip()]
    result = client.upload_file(file_path, plugins=plugins, encrypted=args.encrypted)
    asset_id = result.get("asset_id")
    host = client.host
    url = f"{host}/api/assets/{asset_id}/file"
    thumb_url = f"{host}/api/assets/{asset_id}/thumbnail?size=medium"
    output = {
        **result,
        "url": url,
        "thumbnail_url": thumb_url,
        "markdown": f"![{file_path.name}]({thumb_url})",
        "encrypted": args.encrypted,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def cmd_search(client: AgentClient, args: argparse.Namespace) -> int:
    limit = args.limit
    if args.mode == "semantic":
        result = client.request(
            "GET",
            "/api/search",
            query={"q": args.query, "limit": limit},
        )
        items = result.get("items", [])
        print(f"# query: {result.get('query')} (total {result.get('total', 0)})")
        for item in items[:limit]:
            asset_id = item["asset_id"]
            url = f"{client.host}/api/assets/{asset_id}/thumbnail?size=medium"
            file_url = f"{client.host}/api/assets/{asset_id}/file"
            t = item.get("t_start")
            seek = f"&t_start={t}" if t is not None else ""
            print(f"![asset:{asset_id}]({url}) [{file_url}{seek}]")
        return 0

    # filter mode: map to /api/assets
    query: dict[str, Any] = {"limit": limit}
    if args.tag:
        query["tag"] = args.tag
    if args.category:
        query["category"] = args.category
    if args.person_id is not None:
        query["person_id"] = args.person_id
    if args.media_type:
        query["media_type"] = args.media_type
    if args.favorite is not None:
        query["favorite"] = args.favorite
    result = client.request("GET", "/api/assets", query=query)
    items = result.get("items", [])
    print(f"# filter results ({len(items)} shown)")
    for item in items[:limit]:
        asset_id = item["id"]
        url = f"{client.host}/api/assets/{asset_id}/thumbnail?size=medium"
        file_url = f"{client.host}/api/assets/{asset_id}/file"
        print(f"![asset:{asset_id}]({url}) [{file_url}]")
    return 0


def cmd_test_plugin(client: AgentClient, args: argparse.Namespace) -> int:
    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"file not found: {file_path}", file=sys.stderr)
        return 2
    boundary = "----hometrove-cli-test-boundary"
    with open(file_path, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("latin-1") + data + f"\r\n--{boundary}--\r\n".encode("latin-1")
    result = client.request(
        "POST",
        f"/api/plugins/{args.plugin_id}/test",
        body=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_vault(client: AgentClient, args: argparse.Namespace) -> int:
    """Dispatch vault subcommands: status/setup/unlock/lock."""
    sub = args.vault_cmd
    if sub == "status":
        result = client.request("GET", "/api/vault/status")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if sub == "setup":
        if not args.password or not args.confirm:
            print("--password and --confirm are required for vault setup", file=sys.stderr)
            return 2
        if args.password != args.confirm:
            print("passwords do not match", file=sys.stderr)
            return 2
        result = client.request(
            "POST",
            "/api/vault/setup",
            body=json.dumps({"password": args.password, "confirm": args.confirm}).encode("utf-8"),
            content_type="application/json",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if sub == "unlock":
        if not args.password:
            print("--password is required for vault unlock", file=sys.stderr)
            return 2
        result = client.request(
            "POST",
            "/api/vault/unlock",
            body=json.dumps({"password": args.password}).encode("utf-8"),
            content_type="application/json",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if sub == "lock":
        result = client.request("POST", "/api/vault/lock")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 2


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hometrove-cli",
        description="Agent CLI for HomeTrove — forwards operations to the HTTP API.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOMETROVE_CLI_HOST", "http://127.0.0.1:8080"),
        help="HomeTrove API base URL (default: HOMETROVE_CLI_HOST or http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("HOMETROVE_CLI_API_KEY"),
        help="API key for bearer authentication (default: HOMETROVE_CLI_API_KEY)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # endpoints
    sub.add_parser("endpoints", help="list available CLI commands")

    # describe
    describe_parser = sub.add_parser("describe", help="show parameter details for a command")
    describe_parser.add_argument("command", choices=list(ENDPOINTS.keys()), help="command name")

    # call
    call_parser = sub.add_parser("call", help="call a mapped endpoint generically")
    call_parser.add_argument("command", choices=list(ENDPOINTS.keys()), help="command name")
    call_parser.add_argument(
        "--param",
        dest="params",
        action="append",
        type=_parse_param,
        default=[],
        help="parameter as key=value (repeatable)",
    )

    # upload
    upload_parser = sub.add_parser("upload", help="upload a file and ingest it")
    upload_parser.add_argument("file", help="path to the file to upload")
    upload_parser.add_argument(
        "--plugins",
        help="comma-separated plugin ids to run (default: all enabled plugins)",
    )
    upload_parser.add_argument(
        "--no-encrypted",
        dest="encrypted",
        action="store_false",
        default=True,
        help="store the file in plaintext instead of the vault (default: encrypt when vault is enabled)",
    )

    # search
    search_parser = sub.add_parser("search", help="search assets")
    search_parser.add_argument("query", nargs="?", default="", help="semantic query (for --mode semantic)")
    search_parser.add_argument(
        "--mode",
        choices=["semantic", "filter"],
        default="semantic",
        help="search mode: semantic uses /api/search, filter uses /api/assets",
    )
    search_parser.add_argument("--limit", type=int, default=10, help="max results")
    search_parser.add_argument("--tag", help="filter by tag")
    search_parser.add_argument("--category", help="filter by category")
    search_parser.add_argument("--person-id", type=int, help="filter by person id")
    search_parser.add_argument("--media-type", help="image|video|other")
    search_parser.add_argument("--favorite", type=lambda v: v.lower() in ("true", "1", "yes"), help="true|false")

    # test-plugin
    test_parser = sub.add_parser("test-plugin", help="run a plugin against a file and print JSON")
    test_parser.add_argument("plugin_id", help="plugin id, e.g. basic.info")
    test_parser.add_argument("file", help="path to the test file")

    # vault
    vault_parser = sub.add_parser("vault", help="vault setup / unlock / lock / status")
    vault_sub = vault_parser.add_subparsers(dest="vault_cmd", required=True)
    vault_status = vault_sub.add_parser("status", help="show vault status")
    vault_setup = vault_sub.add_parser("setup", help="initialise a new vault")
    vault_setup.add_argument("--password", required=True, help="vault master password (>=12 chars)")
    vault_setup.add_argument("--confirm", required=True, help="confirm vault master password")
    vault_unlock = vault_sub.add_parser("unlock", help="unlock the vault")
    vault_unlock.add_argument("--password", required=True, help="vault master password")
    vault_sub.add_parser("lock", help="lock the vault")

    args = parser.parse_args(argv)

    if not args.host:
        print("HOMETROVE_CLI_HOST is not set", file=sys.stderr)
        return 2

    client = AgentClient(host=args.host, api_key=args.api_key)

    try:
        if args.cmd == "endpoints":
            return cmd_endpoints(client, args)
        if args.cmd == "describe":
            return cmd_describe(client, args)
        if args.cmd == "call":
            return cmd_call(client, args)
        if args.cmd == "upload":
            return cmd_upload(client, args)
        if args.cmd == "search":
            return cmd_search(client, args)
        if args.cmd == "test-plugin":
            return cmd_test_plugin(client, args)
        if args.cmd == "vault":
            return cmd_vault(client, args)
    except CLIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
