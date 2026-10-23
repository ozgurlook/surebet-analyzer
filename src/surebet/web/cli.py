import os
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from surebet.demo import DEMO_USERNAME, demo_collector_funcs, seed_demo_account
from surebet.storage.db import init_db
from surebet.web.app import create_app

app = typer.Typer(help="Surebet Analyzer Web Application Server")


def _load_env_file(path: Path | str = ".env") -> None:
    """Load key-value pairs from a local .env file if present without overriding existing env vars."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        val = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option("--host", "-h", help="Bind socket host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Bind socket port")] = 8000,
    db: Annotated[Path, typer.Option("--db", help="SQLite DB path")] = Path("surebet.db"),
    background: Annotated[
        bool,
        typer.Option("--background/--no-background", help="Enable automatic background collectors"),
    ] = True,
    demo: Annotated[
        bool,
        typer.Option("--demo", help="Serve synthetic matches and odds; no source is contacted"),
    ] = False,
) -> None:
    """Start the Surebet Web server.

    The first account is created through the registration tab in the web UI
    (enabled only when `SUREBET_REGISTRATION_CODE` is set in the server's
    environment); existing accounts log in from the same screen.

    With `--demo`, every source is replaced by a deterministic fake collector and a
    `demo` login with a freshly printed password is created, so the full UI can be
    explored offline.
    """
    _load_env_file()
    init_db(db)

    if demo:
        password = seed_demo_account(db)
        typer.echo(f"Demo mode: synthetic data only. Log in as {DEMO_USERNAME} / {password}")

    web_app = create_app(
        db_path=db,
        enable_background_runner=background,
        collector_funcs=demo_collector_funcs() if demo else None,
    )

    typer.echo(f"Starting Surebet Web Server at http://{host}:{port}")
    uvicorn.run(web_app, host=host, port=port)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    host: Annotated[str, typer.Option("--host", "-h", help="Bind socket host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Bind socket port")] = 8000,
    db: Annotated[Path, typer.Option("--db", help="SQLite DB path")] = Path("surebet.db"),
    background: Annotated[
        bool,
        typer.Option("--background/--no-background", help="Enable automatic background collectors"),
    ] = True,
    demo: Annotated[
        bool,
        typer.Option("--demo", help="Serve synthetic matches and odds; no source is contacted"),
    ] = False,
) -> None:
    """Start web server by default if no subcommand given."""
    if ctx.invoked_subcommand is None:
        serve(host=host, port=port, db=db, background=background, demo=demo)


if __name__ == "__main__":
    app()
