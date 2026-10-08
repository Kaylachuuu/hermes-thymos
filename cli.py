"""``hermes thymos ...``: read what she has written, and check that moments are being taken."""

from __future__ import annotations

import argparse
import json
import textwrap
import time
from typing import Any, Callable, Dict, List

from . import config as _config
from .store import ERROR, FELT, SKIPPED

USAGE = "usage: hermes thymos {status,log}"


def setup(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="thymos_command")
    subs.add_parser("status", help="Whether moments are being taken, how many, and how long they take")
    log = subs.add_parser("log", help="The latest moments, oldest first")
    log.add_argument("-n", "--limit", type=int, default=10, help="How many to show (default 10)")
    log.add_argument("--errors", action="store_true", help="Only the moments that failed")
    log.add_argument("--skipped", action="store_true", help="Only the moments that were skipped")
    log.add_argument("--raw", action="store_true", help="Also show the model's reply exactly as it came")
    log.add_argument("--json", action="store_true", help="Print as JSON")


def make_handler(thymos: Any) -> Callable[[argparse.Namespace], int]:
    def handler(args: argparse.Namespace) -> int:
        command = getattr(args, "thymos_command", None)
        if command == "status":
            print(status_text(thymos.config(), thymos.store().summary(), str(thymos.store().path)))
            return 0
        if command == "log":
            only = ERROR if args.errors else SKIPPED if args.skipped else None
            rows = thymos.store().recent(args.limit, status=only)
            print(json.dumps(rows, indent=2, ensure_ascii=False) if args.json else log_text(rows, raw=args.raw))
            return 0
        print(USAGE)
        return 2
    return handler


def _when(stamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))


def status_text(cfg: Dict[str, Any], summary: Dict[str, Any], path: str) -> str:
    lines: List[str] = []
    if not cfg["enabled"]:
        lines.append("Thymos is switched off (enabled: false). No moments are being taken.")
    elif cfg["visibility"] != _config.OPEN:
        lines.append("Visibility is set to sealed. Sealed entries are not built in this version, so no "
                     "moments are being taken. Set visibility to open to take them.")
    else:
        lines.append("Thymos is on. Moments are open: she is told that you can read them.")
    lines.append(f"Stored in:   {path}")
    lines.append(f"Moments:     {summary['felt']} felt, {summary['errors']} failed, {summary['skipped']} skipped")
    if summary["felt"]:
        lines.append(f"Last one:    {_when(summary['last_felt_at'])}")
        lines.append(f"Time taken:  {summary['average_ms'] / 1000:.1f} s on average")
        if summary["average_intensity"] is not None:
            lines.append(f"Intensity:   {summary['average_intensity']:.1f} on average (0 to 10)")
        if summary["without_number"]:
            lines.append(f"No number:   {summary['without_number']} answered in words but gave no intensity line")
        if summary["models"]:
            lines.append(f"Answered by: {', '.join(summary['models'])}")
            if len(summary["models"]) > 1:
                lines.append("             More than one model has answered. The first listed is the latest.")
    return "\n".join(lines)


def log_text(rows: List[Dict[str, Any]], *, raw: bool = False) -> str:
    if not rows:
        return "Nothing yet."
    out: List[str] = []
    for row in rows:
        head = f"#{row['id']}  {_when(row['created_at'])}"
        if row["platform"]:
            head += f"  {row['platform']}"
        if row["status"] == FELT:
            head += "  intensity " + ("-" if row["intensity"] is None else f"{row['intensity']:g}")
            head += f"  {row['duration_ms'] / 1000:.1f} s"
        else:
            head += f"  {row['status'].upper()}: {row['note']}"
        out.append(head)
        if row["status"] == FELT:
            body = row["words"] if row["readable"] else "(sealed: hers until she shares it)"
            out.append(textwrap.indent(body, "    "))
        if raw and row["readable"] and row["raw"] and row["raw"].strip() != row["words"].strip():
            out.append(textwrap.indent("as it came:\n" + row["raw"].strip(), "      "))
        out.append("")
    return "\n".join(out).rstrip()
