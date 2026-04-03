#!/usr/bin/env python3
"""
Command-line interface for Correos de Costa Rica tracking.

Usage:
  python3 correos_cli.py TRACKING_NUMBER
  python3 correos_cli.py TRACKING_NUMBER1 TRACKING_NUMBER2 ...
"""

import argparse
import asyncio
import re
from pathlib import Path

from tracker import CorreosTracker

def _create_tracker(credentials_file: Path) -> CorreosTracker:
    content = credentials_file.read_text()
    get = lambda key: re.search(rf"^{key}:\s*(.+)$", content, re.MULTILINE)
    user = get("user").group(1).strip()
    pw = get("pw").group(1).strip()
    return CorreosTracker(user, pw)


def _format_single_result(result) -> str:
    if result.status == "not_found":
        return (
            f"📦 Tracking Details — {result.tracking_number}\n\n"
            f"❌ Status: Envío sin registrar en Correos de Costa Rica\n\n"
            f"La guía no aparece en el sistema de Correos.\n"
            f"Esto puede significar que:\n"
            f"• El remitente aún no la ha registrado\n"
            f"• Está en camino a la oficina\n"
            f"• Aún no ha sido procesada"
        )

    badge = "✅ DELIVERED" if result.status == "delivered" else "🟡 IN TRANSIT"
    dest = result.destination or "No disponible"

    if result.events:
        events_text = "\n".join(
            f"  • [{e['fecha']} {e['hora']}] {e['sucursal']} — {e['descripcion']}"
            for e in result.events
        )
        events_block = f"\n\n📍 Historial:\n{events_text}"
    else:
        events_block = "\n\n📍 Historial: Sin eventos registrados"

    return (
        f"📦 Tracking Details — {result.tracking_number}\n\n"
        f"🏷️ Status: {badge}\n"
        f"📋 Estado: {result.status_human or 'No disponible'}\n"
        f"📍 Destinatario: {dest}"
        f"{events_block}"
    )


def _format_report(results) -> str:
    lines = [
        f"📊 Status Report — {len(results)} guías",
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
    lines.append(
        f"Total: {len(results)} | ✅ {delivered} | 🟡 {in_transit} | ❌ {not_found}"
    )
    return "\n".join(lines)


async def _run(credentials_file: Path, tracking_numbers: list[str]) -> int:
    tracker = _create_tracker(credentials_file)
    try:
        if len(tracking_numbers) == 1:
            result = await tracker.track(tracking_numbers[0])
            print(_format_single_result(result))
        else:
            results = await tracker.track_multiple(tracking_numbers)
            print(_format_report(results))
        return 0
    finally:
        await tracker.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="correos_cli",
        description="Track Correos de Costa Rica shipments from the command line.",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default="./credentials.md",
        nargs='?',
        help="Path to the credentials file (default: ./credentials.md)",
    )
    parser.add_argument(
        "tracking_numbers",
        nargs="+",
        help="One or more tracking numbers to query",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.credentials, args.tracking_numbers))


if __name__ == "__main__":
    raise SystemExit(main())