"""`pais support-bundle` — one-shot zip of doctor report + chat errors + log.

Designed to be pasted into a support ticket. Optionally reproduces a failing
chat turn first (`--chat <agent_id> --file <prompt.md>`) so the dump is fresh.

Usage:
    pais support-bundle                             # package whatever's already on disk
    pais support-bundle --chat <id> --file p.md     # reproduce a failure, then bundle
    pais support-bundle -o /tmp/bug.zip             # write to a specific path
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import zipfile
from pathlib import Path

import typer

from pais.cli._flags import HELP_OPTION_NAMES
from pais.cli._output import render
from pais.cli.doctor_cmd import _dump_settings
from pais.config import Settings

_LOG_DIR = Path.home() / ".pais" / "logs"


def support_bundle(
    chat: str | None = typer.Option(
        None, "--chat", help="Agent id to reproduce a failing chat against before bundling."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-F", help="Prompt file to send (only with --chat)."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Zip output path (default: /tmp/pais-bundle-<ts>.zip)."
    ),
) -> None:
    """Zip a deployment snapshot + recent chat errors + the local log into one file."""
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if chat is not None and file is None:
        raise typer.BadParameter("--chat requires --file PATH (the prompt to send).")

    # 1. Fresh doctor snapshot — reuses the probe battery from doctor_cmd.
    from pais.cli.doctor_cmd import doctor as _doctor_fn

    typer.echo("↻ running pais doctor …", err=True)
    with contextlib.suppress(typer.Exit):
        _doctor_fn(output="json")

    # 2. Optional: reproduce a failing chat.
    if chat is not None:
        assert file is not None
        typer.echo(f"↻ reproducing chat against {chat} with {file.name} …", err=True)
        from pais.client import PaisClient
        from pais.models import ChatCompletionRequest, ChatMessage

        settings = Settings()
        content = file.expanduser().read_text(encoding="utf-8")
        try:
            with PaisClient.from_settings(settings) as c:
                c.agents.chat(
                    chat,
                    ChatCompletionRequest(messages=[ChatMessage(role="user", content=content)]),
                )
            typer.echo("✓ chat succeeded (no error to capture)", err=True)
        except Exception as e:
            from pais.cli._error_dump import dump_chat_error

            dump_path = dump_chat_error(e, agent_id=chat, prompt=content)
            typer.echo(f"✓ captured failure → {dump_path}", err=True)

    # 3. Collect artifacts.
    out_path = output or Path("/tmp") / f"pais-bundle-{ts}.zip"
    out_path = out_path.expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doctor_reports = sorted(_LOG_DIR.glob("doctor-*.md"))
    latest_doctor = doctor_reports[-1] if doctor_reports else None
    chat_errors = (
        sorted((_LOG_DIR / "chat-errors").glob("*.json"))
        if (_LOG_DIR / "chat-errors").exists()
        else []
    )
    pais_log = _LOG_DIR / "pais.log"

    included: list[str] = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        if latest_doctor is not None:
            z.write(latest_doctor, arcname=latest_doctor.name)
            included.append(latest_doctor.name)
        for ce in chat_errors:
            z.write(ce, arcname=f"chat-errors/{ce.name}")
            included.append(f"chat-errors/{ce.name}")
        if pais_log.exists():
            z.write(pais_log, arcname="pais.log")
            included.append("pais.log")

    render(
        {
            "bundle": str(out_path),
            "included": included,
            "doctor_report": str(latest_doctor) if latest_doctor else None,
            "chat_errors": len(chat_errors),
            "settings_snapshot": _dump_settings(Settings()),
        },
        fmt="json",
    )


app = typer.Typer(add_completion=False, context_settings=HELP_OPTION_NAMES)
app.command(name="support-bundle")(support_bundle)
