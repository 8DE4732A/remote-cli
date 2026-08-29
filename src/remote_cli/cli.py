"""Main CLI entrypoint for remote-cli."""

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .client import Client
from .daemon import ensure_daemon_running, stop_daemon
from .terminal import attach_session, get_terminal_size
from .utils import get_base_dir, get_log_path, get_pid_path

app = typer.Typer(
    name="remote-cli",
    help="Shared SSH & terminal CLI tool for AI Agent and human co-piloting.",
    no_args_is_help=True,
)
session_app = typer.Typer(help="Manage terminal sessions.")
daemon_app = typer.Typer(help="Manage the remote-cli background daemon.")

app.add_typer(session_app, name="session")
app.add_typer(daemon_app, name="daemon")

console = Console()
err_console = Console(stderr=True)


@app.command(
    "ssh",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Start an SSH session, get a Session ID for your Agent, and attach immediately.",
)
def ssh_command(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", "-n", help="Optional name for this session"),
):
    """Start an interactive SSH session shared with your AI Agent."""
    ssh_args = ctx.args
    if not ssh_args:
        err_console.print(
            "[bold red]Error:[/bold red] Please provide SSH arguments (e.g. `remote-cli ssh user@host`)"
        )
        raise typer.Exit(1)

    cmd = ["ssh"] + ssh_args
    ensure_daemon_running()
    client = Client()

    rows, cols = get_terminal_size()
    session = client.create_session(command=cmd, name=name, rows=rows, cols=cols)

    console.print(
        Panel.fit(
            f"[bold green]Session Created:[/bold green] [bold yellow]{session.session_id}[/bold yellow]\n"
            f'[cyan]Agent Command:[/cyan] [white]remote-cli exec {session.session_id} "<command>"[/white]\n'
            f"[dim]Press [bold]Ctrl+][/bold] to detach from session at any time.[/dim]",
            title="[bold]remote-cli SSH Session[/bold]",
            border_style="green",
        )
    )

    attach_session(session.session_id)


@session_app.command(
    "create",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def session_create(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", "-n", help="Optional session name"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Do not attach immediately"),
):
    """Create a new shell session (default: /bin/bash or custom command)."""
    command = ctx.args if ctx.args else ["/bin/bash"]
    ensure_daemon_running()
    client = Client()

    rows, cols = get_terminal_size()
    session = client.create_session(command=command, name=name, rows=rows, cols=cols)

    if detach:
        console.print(f"[green]Created session:[/green] [bold]{session.session_id}[/bold]")
        console.print(f"Attach with: [cyan]remote-cli session attach {session.session_id}[/cyan]")
    else:
        console.print(
            Panel.fit(
                f"[bold green]Session Created:[/bold green] [bold yellow]{session.session_id}[/bold yellow]\n"
                f'[cyan]Agent Command:[/cyan] [white]remote-cli exec {session.session_id} "<command>"[/white]\n'
                f"[dim]Press [bold]Ctrl+][/bold] to detach.[/dim]",
                title="[bold]remote-cli Session[/bold]",
                border_style="green",
            )
        )
        attach_session(session.session_id)


@session_app.command("list")
def session_list():
    """List all active and recent sessions."""
    ensure_daemon_running()
    client = Client()
    sessions = client.list_sessions()

    if not sessions:
        console.print("[dim]No active sessions found.[/dim]")
        return

    table = Table(title="remote-cli Sessions")
    table.add_column("Session ID", style="bold yellow")
    table.add_column("Name", style="cyan")
    table.add_column("Command", style="white")
    table.add_column("Status", style="bold")
    table.add_column("Clients", justify="center")
    table.add_column("Created At", style="dim")

    for s in sessions:
        status_str = (
            f"[green]{s.status}[/green]" if s.status == "active" else f"[red]{s.status}[/red]"
        )
        cmd_str = " ".join(s.command)
        table.add_row(
            s.session_id,
            s.name,
            cmd_str,
            status_str,
            str(s.attached_clients),
            s.created_at,
        )

    console.print(table)


@session_app.command("attach")
def session_attach(session_id: str):
    """Attach terminal to an existing session."""
    ensure_daemon_running()
    client = Client()
    session = client.get_session(session_id)
    if not session:
        err_console.print(f"[red]Error:[/red] Session {session_id} not found.")
        raise typer.Exit(1)
    if session.status != "active":
        err_console.print(f"[red]Error:[/red] Session {session_id} has already exited.")
        raise typer.Exit(1)

    console.print(f"[dim]Attaching to {session_id}... (Press Ctrl+] to detach)[/dim]")
    attach_session(session_id)


@session_app.command("close")
def session_close(session_id: str):
    """Close and terminate a session."""
    ensure_daemon_running()
    client = Client()
    if client.close_session(session_id):
        console.print(f"[green]Session {session_id} closed.[/green]")
    else:
        err_console.print(f"[red]Error:[/red] Session {session_id} not found.")
        raise typer.Exit(1)


# Shortcut commands at root level
@app.command("ls")
def ls_shortcut():
    """Shortcut for `session list`."""
    session_list()


@app.command("attach")
def attach_shortcut(session_id: str):
    """Shortcut for `session attach`."""
    session_attach(session_id)


@app.command("exec")
def exec_command(
    session_id: str = typer.Argument(..., help="Target session ID"),
    command: str = typer.Argument(..., help="Shell command to execute"),
    timeout: float = typer.Option(30.0, "--timeout", "-t", help="Timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
):
    """Execute a command in the session and capture output and return code."""
    ensure_daemon_running()
    client = Client()
    try:
        res = client.exec_command(session_id, command, timeout=timeout)
    except Exception as e:
        if json_output:
            console.print(json.dumps({"success": False, "error": str(e)}))
        else:
            err_console.print(f"[red]Execution error:[/red] {e}")
        raise typer.Exit(1) from None

    if json_output:
        print(res.model_dump_json(indent=2))
    else:
        if res.output:
            print(res.output)
        if res.timed_out:
            err_console.print(f"[bold red]Command timed out after {timeout}s[/bold red]")
            raise typer.Exit(124)
        if res.exit_code is not None and res.exit_code != 0:
            raise typer.Exit(res.exit_code)


@app.command("send")
def send_command(
    session_id: str = typer.Argument(..., help="Target session ID"),
    text: str | None = typer.Argument(None, help="Text to send"),
    no_newline: bool = typer.Option(False, "--no-newline", "-n", help="Do not append newline"),
    ctrl_c: bool = typer.Option(False, "--ctrl-c", help="Send Ctrl+C interrupt"),
    ctrl_d: bool = typer.Option(False, "--ctrl-d", help="Send Ctrl+D EOF"),
):
    """Send raw text, keys, or control signals (Ctrl+C, Ctrl+D) to the session."""
    ensure_daemon_running()
    client = Client()
    try:
        success = client.send_input(
            session_id=session_id,
            text=text,
            no_newline=no_newline,
            ctrl_c=ctrl_c,
            ctrl_d=ctrl_d,
        )
        if not success:
            err_console.print("[red]Failed to send input.[/red]")
            raise typer.Exit(1)
    except Exception as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None


@app.command("snapshot")
def snapshot_command(
    session_id: str = typer.Argument(..., help="Target session ID"),
    raw: bool = typer.Option(False, "--raw", help="Keep trailing empty lines"),
):
    """Capture the 2D terminal screen state."""
    ensure_daemon_running()
    client = Client()
    try:
        screen_text = client.snapshot(session_id, clean=not raw)
        print(screen_text)
    except Exception as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None


@app.command("logs")
def logs_command(
    session_id: str = typer.Argument(..., help="Target session ID"),
    lines: int = typer.Option(100, "--lines", "-n", help="Number of lines to retrieve"),
):
    """View recent output scrollback logs."""
    ensure_daemon_running()
    client = Client()
    try:
        log_lines = client.logs(session_id, lines=lines)
        for line in log_lines:
            print(line)
    except Exception as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from None


@daemon_app.command("start")
def daemon_start():
    """Start the daemon process explicitly in foreground or background."""
    ensure_daemon_running()
    console.print(f"[green]remote-cli daemon is running.[/green] Log: {get_log_path()}")


@daemon_app.command("stop")
def daemon_stop():
    """Stop the running daemon process."""
    if stop_daemon():
        console.print("[green]remote-cli daemon stopped.[/green]")
    else:
        console.print("[yellow]remote-cli daemon is not running.[/yellow]")


@daemon_app.command("status")
def daemon_status():
    """Check the status of the remote-cli daemon."""
    client = Client()
    if client.ping():
        pid = get_pid_path().read_text().strip() if get_pid_path().exists() else "unknown"
        console.print(f"[green]Daemon is running[/green] (PID: {pid})")
        console.print(f"Base Directory: {get_base_dir()}")
        console.print(f"Log File: {get_log_path()}")
    else:
        console.print("[yellow]Daemon is stopped.[/yellow]")


def main():
    app()


if __name__ == "__main__":
    main()
