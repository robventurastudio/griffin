"""Lightweight loader and executor for codeword-driven trading commands."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass
class CommandAction:
    """Represents a single actionable command from the sheet."""

    codeword: str
    action: str
    symbol: str | None = None
    qty: int | None = None
    side: str | None = None
    note: str | None = None

    def normalized_codeword(self) -> str:
        return self.codeword.strip().upper()


def _coerce_dict_entry(codeword: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    merged = {"codeword": codeword}
    merged.update(entry)
    return merged


def _parse_entry(entry: Dict[str, Any]) -> CommandAction:
    required = {"codeword", "action"}
    missing = required - set(entry)
    if missing:
        raise ValueError(f"Missing required fields {sorted(missing)} in command entry {entry!r}")

    qty = entry.get("qty")
    if qty is not None:
        qty = int(qty)

    action = str(entry["action"]).lower()
    side = (entry.get("side") or "").lower() or None
    if side is None and action in {"buy", "market_buy"}:
        side = "buy"
    if side is None and action in {"sell", "market_sell"}:
        side = "sell"

    return CommandAction(
        codeword=str(entry["codeword"]),
        action=action,
        symbol=entry.get("symbol"),
        qty=qty,
        side=side,
        note=entry.get("note"),
    )


def load_command_sheet(path: str | Path) -> Dict[str, CommandAction]:
    """Load a JSON sheet of codeword commands.

    The file can be shaped as either::

        {"commands": [{"codeword": "BUY100_SPY", "action": "market_order", ...}, ...]}

    or a mapping keyed by codeword::

        {"BUY100_SPY": {"action": "market_order", "symbol": "SPY", "qty": 100, "side": "buy"}}
    """

    data = json.loads(Path(path).read_text())
    commands_raw: Any
    if isinstance(data, dict) and "commands" in data:
        commands_raw = data["commands"]
    else:
        commands_raw = data

    entries: list[Dict[str, Any]] = []
    if isinstance(commands_raw, dict):
        for codeword, payload in commands_raw.items():
            entries.append(_coerce_dict_entry(codeword, payload))
    elif isinstance(commands_raw, list):
        entries = list(commands_raw)
    else:
        raise ValueError("Command sheet must be a list or mapping")

    parsed: Dict[str, CommandAction] = {}
    for entry in entries:
        cmd = _parse_entry(entry)
        parsed[cmd.normalized_codeword()] = cmd

    return parsed


def _resolve_order_details(cmd: CommandAction) -> tuple[str, int, str]:
    action = cmd.action
    side = cmd.side
    if action in {"buy", "market_buy"}:
        side = "buy"
    elif action in {"sell", "market_sell"}:
        side = "sell"
    elif action not in {"market_order", "close_all", "flatten"}:
        raise ValueError(f"Unsupported action '{cmd.action}' for codeword {cmd.codeword}")

    if action in {"close_all", "flatten"}:
        return ("close_all", 0, "")

    if side is None:
        raise ValueError(f"Command {cmd.codeword} requires a side (buy/sell)")
    if cmd.symbol is None or cmd.qty is None:
        raise ValueError(f"Command {cmd.codeword} requires both symbol and qty")

    return cmd.symbol, int(cmd.qty), side


def execute_command(
    codeword: str, client, commands: Dict[str, CommandAction], *, dry_run: bool = False
) -> CommandAction:
    """Execute a codeword command using the provided client.

    Returns the resolved :class:`CommandAction` for logging/reporting.
    """

    normalized = codeword.strip().upper()
    if normalized not in commands:
        raise KeyError(f"Unknown codeword '{codeword}'")

    cmd = commands[normalized]
    symbol, qty, side = _resolve_order_details(cmd)

    if symbol == "close_all":
        if not dry_run:
            client.close_all_positions()
        return cmd

    if not dry_run:
        client.submit_order(symbol, qty, side)

    return cmd


def format_commands(commands: Dict[str, CommandAction]) -> list[str]:
    """Render a concise list of commands for CLI display."""

    rows: list[str] = []
    header = f"{'Codeword':<20} {'Action':<15} {'Symbol':<8} {'Qty':<6} Note"
    rows.append(header)
    rows.append("-" * len(header))

    for codeword in sorted(commands):
        cmd = commands[codeword]
        note = cmd.note or ""
        symbol = cmd.symbol or "-"
        qty = cmd.qty if cmd.qty is not None else "-"
        rows.append(f"{cmd.codeword:<20} {cmd.action:<15} {symbol:<8} {qty!s:<6} {note}")

    return rows
