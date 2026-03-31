#!/usr/bin/env python3
"""
MCP Server for Correos de Costa Rica tracking.
Tools: track_details, get_status

Usage: python3 correos_mcp.py
(stdio transport — connect via MCP client)
"""

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from playwright.async_api import async_playwright


# ── Config ──────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent.parent / "credentials" / "correos.md"


def load_credentials() -> tuple[str, str]:
    content = CONFIG_FILE.read_text()
    get = lambda key: re.search(rf"^{key}:\s*(.+)$", content, re.MULTILINE)
    user = get("user").group(1).strip()
    pw = get("pw").group(1).strip()
    return user, pw


# ── Data model ─────────────────────────────────────────────────
@dataclass
class TrackingResult:
    tracking_number: str
    status: str  # in_transit | delivered | not_found
    status_human: str | None
    destination: str | None
    events: list[dict]
    raw_text: str | None


# ── Correos Client ─────────────────────────────────────────────
class CorreosTracker:
    BASE_URL = "https://sucursal.correos.go.cr"
    LOGIN_URL = f"{BASE_URL}/login"
    TRACKING_URL = f"{BASE_URL}/sucursal/tracking"

    def __init__(self, user: str, pw_val: str):
        self.user = user
        self._pw = pw_val
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def _ensure_session(self):
        if self._page is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()
            await self._login()

    async def _login(self):
        page = self._page
        await page.goto(self.LOGIN_URL, wait_until="networkidle")
        await page.fill('input[name=login]', self.user)
        await page.fill('input[name=password]', self._pw)
        await page.click('button[type=submit]')
        await page.wait_for_url("**/home**", timeout=15000)

    def _parse_tracking_page(self, text: str) -> tuple[str | None, str | None, str | None, list[dict]]:
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        # Human-readable status
        estado_match = re.search(r"Estado Actual\s*\n\s*(.+)", text)
        status_human = estado_match.group(1).strip() if estado_match else None

        # Normalize to enum
        hl = (status_human or "").lower()
        hl = unicodedata.normalize("NFD", hl)
        hl = re.sub(r"[\u0300-\u036f]", "", hl)

        if "transit" in hl:
            status = "in_transit"
        elif "entregad" in hl or "delivered" in hl:
            status = "delivered"
        else:
            status = "not_found"

        # Destination
        dest_match = re.search(
            r"Dirección\s+destinatario\s*\n\s*(.+?)(?=\s*\n|Ver\s+Ubicación)",
            text,
        )
        destination = dest_match.group(1).strip() if dest_match else None

        # Events: look for "SUCURSAL XXXX - DD/MM/YY HH:MM AM/PM" pattern
        events = []
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            em = re.match(
                r"^SUCURSAL\s+(.+?)\s*-\s*(\d{2}/\d{2}/\d{2})\s+(\d{2}:\d{2})\s*(AM|PM)$",
                line,
            )
            if em and idx > 0:
                desc = lines[idx - 1]
                if not re.match(r"^(Estado|Dirección|Fecha|Número)", desc):
                    events.append({
                        "sucursal": "SUCURSAL " + em.group(1).strip(),
                        "fecha": em.group(2),
                        "hora": em.group(3) + " " + em.group(4),
                        "descripcion": desc[:200],
                    })
                    idx -= 1  # skip the description line (already consumed)
            idx += 1

        return status, status_human, destination, list(reversed(events))

    async def track(self, tracking_number: str) -> TrackingResult:
        await self._ensure_session()
        page = self._page

        await page.goto(self.TRACKING_URL, wait_until="networkidle")
        await page.wait_for_timeout(500)

        await page.fill('input[name=tracking]', tracking_number)
        await page.click('button[type=submit]')

        # Form submit is a full page reload — wait fixed time for DOM to update
        await page.wait_for_timeout(5000)

        text = await page.evaluate("document.body.innerText")

        # Check for "not registered" state
        lower_text = text.lower()
        if ("no ha iniciado el trayecto" in lower_text or
                "sin registrar" in lower_text or
                "no se encontraron" in lower_text):
            return TrackingResult(
                tracking_number=tracking_number,
                status="not_found",
                status_human="Envío sin registrar en Correos de Costa Rica",
                destination=None,
                events=[],
                raw_text=text,
            )

        status, status_human, destination, events = self._parse_tracking_page(text)

        return TrackingResult(
            tracking_number=tracking_number,
            status=status or "unknown",
            status_human=status_human,
            destination=destination,
            events=events,
            raw_text=text,
        )

    async def track_multiple(self, tracking_numbers: list[str]) -> list[TrackingResult]:
        await self._ensure_session()
        results = []
        for tn in tracking_numbers:
            result = await self.track(tn)
            results.append(result)
            await asyncio.sleep(2)  # rate limit
        return results

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()


# ── MCP Server ─────────────────────────────────────────────────
APP_NAME = "correos-mcp"
APP_VERSION = "1.0.0"

server = Server(APP_NAME, APP_VERSION)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="track_details",
            description=(
                "Get detailed tracking information for a single Correos de Costa Rica shipment. "
                "Returns status, destination, and full event history."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tracking_number": {
                        "type": "string",
                        "description": "Correos tracking number (e.g. PY069505428CR)",
                    }
                },
                "required": ["tracking_number"],
            },
        ),
        Tool(
            name="get_status",
            description=(
                "Get current status (in_transit, delivered, or not_found) for multiple "
                "Correos de Costa Rica shipments at once."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tracking_numbers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of Correos tracking numbers",
                    }
                },
                "required": ["tracking_numbers"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    user, pw = load_credentials()
    tracker = CorreosTracker(user, pw)  # pw is (user, pw) tuple from load_credentials

    try:
        if name == "track_details":
            tn = arguments["tracking_number"]
            result = await tracker.track(tn)

            if result.status == "not_found":
                text = (
                    f"📦 *Tracking Details — {tn}*\n\n"
                    f"❌ *Status:* Envío sin registrar en Correos de Costa Rica\n\n"
                    f"La guía no aparece en el sistema de Correos. "
                    f"Esto puede significar que:\n"
                    f"• El remitente aún no la ha registrado\n"
                    f"• Está en camino a la oficina\n"
                    f"• Aún no ha sido procesada"
                )
            else:
                badge = (
                    "✅ DELIVERED"
                    if result.status == "delivered"
                    else "🟡 IN TRANSIT"
                )
                dest = result.destination or "No disponible"

                if result.events:
                    events_text = (
                        "\n\n📍 *Historial:*\n"
                        + "\n".join(
                            f"  • [{e['fecha']} {e['hora']}] {e['sucursal']} — {e['descripcion']}"
                            for e in result.events
                        )
                    )
                else:
                    events_text = "\n\n📍 *Historial:* Sin eventos registrados"

                text = (
                    f"📦 *Tracking Details — {tn}*\n\n"
                    f"🏷️ *Status:* {badge}\n"
                    f"📋 *Estado:* {result.status_human or 'No disponible'}\n"
                    f"📍 *Destinatario:* {dest}"
                    f"{events_text}"
                )

            return [TextContent(type="text", text=text)]

        elif name == "get_status":
            tns = arguments["tracking_numbers"]
            results = await tracker.track_multiple(tns)

            lines = [
                f"📊 *Status Report — {len(tns)} guías*\n",
                f"{'─' * 42}",
                f"{'Guía':<20} Status",
                f"{'─' * 42}",
            ]
            for r in results:
                badge = (
                    "✅ DELIVERED"
                    if r.status == "delivered"
                    else "❌ NOT FOUND"
                    if r.status == "not_found"
                    else "🟡 IN TRANSIT"
                )
                lines.append(f"{r.tracking_number:<20} {badge}")

            delivered = sum(1 for r in results if r.status == "delivered")
            in_transit = sum(1 for r in results if r.status == "in_transit")
            not_found = sum(1 for r in results if r.status == "not_found")
            lines.append(f"{'─' * 42}")
            lines.append(f"Total: {len(results)} | ✅ {delivered} | 🟡 {in_transit} | ❌ {not_found}")

            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    finally:
        await tracker.close()


# ── Main ──────────────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
