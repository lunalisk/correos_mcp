#!/usr/bin/env python3
"""
MCP Server for Correos de Costa Rica tracking.
Tools: track_details, status_report

Usage: python3 correos_mcp.py
(stdio transport — connect via MCP client)
"""

import re
from pathlib import Path

from fastmcp import FastMCP
from mcp.types import TextContent

from tracker import CorreosTracker


CONFIG_FILE = Path(__file__).parent.parent / "credentials" / "correos.md"

def _create_tracker():
    content = CONFIG_FILE.read_text()
    get = lambda key: re.search(rf"^{key}:\s*(.+)$", content, re.MULTILINE)
    user = get("user").group(1).strip()
    pw = get("pw").group(1).strip()
    return CorreosTracker(user, pw)

mcp = FastMCP(name= "correos-mcp", version="1.0.0")

@mcp.tool()
async def track_details(tracking_number: str):
    tracker = _create_tracker()
    try:
        result = await tracker.track(tracking_number)
        if result.status == "not_found":
            text = (
                f"📦 *Tracking Details — {tracking_number}*\n\n"
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
                f"📦 *Tracking Details — {tracking_number}*\n\n"
                f"🏷️ *Status:* {badge}\n"
                f"📋 *Estado:* {result.status_human or 'No disponible'}\n"
                f"📍 *Destinatario:* {dest}"
                f"{events_text}"
            )

        return [TextContent(type="text", text=text)]
    finally:
        await tracker.close()

@mcp.tool()
async def status_report(tracking_numbers: list[str]):
    tracker = _create_tracker()
    try:
        results = await tracker.track_multiple(tracking_numbers)

        lines = [
            f"📊 *Status Report — {len(tracking_numbers)} guías*\n",
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
    finally:
        await tracker.close()


if __name__ == "__main__":
    mcp.run()
