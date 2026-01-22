"""
OddOpp Interactive Terminal Application

A Rich + Typer based terminal UI for betting analytics.
"""

import asyncio
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.table import Table

from .db.models import init_db, get_session, Event, Odds, Provider, Bet, Profile
from .factory import ExtractorFactory
from .pipeline import ExtractionPipeline
from .analysis import find_arbitrage, find_best_value

console = Console()
app = typer.Typer(help="OddOpp - Betting Analytics Platform")


def show_banner():
    """Display the app banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                      OddOpp                               ║
    ║           Betting Analytics Platform                      ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    console.print(Panel(banner, style="blue"))


def show_stats():
    """Show database statistics."""
    session = get_session()

    total_events = session.query(Event).count()
    total_odds = session.query(Odds).count()
    total_providers = session.query(Provider).count()

    # Count matched events
    from sqlalchemy import func
    matched = session.query(Event).join(Odds).group_by(Event.id).having(
        func.count(func.distinct(Odds.provider_id)) > 1
    ).count()

    table = Table(title="Database Statistics", show_header=False)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Total Events", str(total_events))
    table.add_row("Total Odds", str(total_odds))
    table.add_row("Providers", str(total_providers))
    table.add_row("Matched Events", str(matched))

    session.close()
    console.print(table)


def show_arbitrage():
    """Show arbitrage opportunities."""
    session = get_session()

    # Get events with multiple providers
    from sqlalchemy import func
    matched_events = session.query(Event).join(Odds).group_by(Event.id).having(
        func.count(func.distinct(Odds.provider_id)) > 1
    ).limit(50).all()

    if not matched_events:
        console.print("[yellow]No matched events found. Run 'extract' first.[/yellow]")
        return

    # Find arbitrage opportunities
    arbs = []
    for event in matched_events:
        result = find_arbitrage(event, session)
        if result and result.get("profit_pct", 0) > 0:
            arbs.append(result)

    if not arbs:
        console.print("[yellow]No arbitrage opportunities found.[/yellow]")
        return

    table = Table(title="Arbitrage Opportunities")
    table.add_column("Event", style="cyan")
    table.add_column("Market", style="white")
    table.add_column("Provider 1", style="green")
    table.add_column("Provider 2", style="green")
    table.add_column("Profit %", style="yellow")

    for arb in sorted(arbs, key=lambda x: x.get("profit_pct", 0), reverse=True)[:10]:
        table.add_row(
            f"{arb['home']} vs {arb['away']}"[:30],
            arb.get("market", "-"),
            arb.get("provider1", "-"),
            arb.get("provider2", "-"),
            f"{arb.get('profit_pct', 0):.2f}%",
        )

    session.close()
    console.print(table)


def show_value_bets():
    """Show value betting opportunities."""
    session = get_session()

    # Get events with Polymarket odds
    poly_events = session.query(Event).join(Odds).filter(
        Odds.provider_id == "polymarket"
    ).distinct().limit(50).all()

    if not poly_events:
        console.print("[yellow]No Polymarket events found. Run 'extract' first.[/yellow]")
        return

    # Find value bets
    values = []
    for event in poly_events:
        result = find_best_value(event, session)
        if result and result.get("edge_pct", 0) > 2:  # Min 2% edge
            values.append(result)

    if not values:
        console.print("[yellow]No value bets found (min 2% edge).[/yellow]")
        return

    table = Table(title="Value Bets (vs Polymarket)")
    table.add_column("Event", style="cyan")
    table.add_column("Outcome", style="white")
    table.add_column("Provider", style="green")
    table.add_column("Odds", style="yellow")
    table.add_column("Fair Odds", style="blue")
    table.add_column("Edge %", style="magenta")

    for val in sorted(values, key=lambda x: x.get("edge_pct", 0), reverse=True)[:10]:
        table.add_row(
            f"{val['home']} vs {val['away']}"[:25],
            val.get("outcome", "-"),
            val.get("provider", "-"),
            f"{val.get('odds', 0):.2f}",
            f"{val.get('fair_odds', 0):.2f}",
            f"{val.get('edge_pct', 0):.1f}%",
        )

    session.close()
    console.print(table)


def show_providers():
    """Show configured providers."""
    factory = ExtractorFactory.get_instance()

    table = Table(title="Configured Providers")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Type", style="green")
    table.add_column("Domain", style="blue")

    for pid, config in factory.providers.items():
        table.add_row(
            pid,
            config.get("name", pid),
            config.get("retriever_type", "-"),
            config.get("domain", "-"),
        )

    console.print(table)


async def run_extraction(providers: Optional[list] = None, skip_poly: bool = False):
    """Run the extraction pipeline."""
    init_db()
    pipeline = ExtractionPipeline()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting...", total=None)

        def on_progress(msg: str):
            progress.update(task, description=msg)

        results = await pipeline.run(
            polymarket=not skip_poly,
            providers=providers,
            on_progress=on_progress,
        )

    # Show results
    console.print("\n[green]Extraction Complete![/green]")

    table = Table(title="Extraction Results")
    table.add_column("Source", style="cyan")
    table.add_column("Events", style="white")
    table.add_column("New Odds", style="green")

    if not skip_poly:
        poly = results.get("polymarket", {})
        table.add_row(
            "Polymarket",
            str(poly.get("events_processed", 0)),
            str(poly.get("odds_new", 0)),
        )

    for pid, data in results.get("providers", {}).items():
        table.add_row(
            pid,
            str(data.get("events_processed", 0)),
            str(data.get("odds_new", 0)),
        )

    console.print(table)
    console.print(f"\n[cyan]Total Events:[/cyan] {results.get('total_events', 0)}")
    console.print(f"[cyan]Matched Events:[/cyan] {results.get('matched_events', 0)}")


def show_settings():
    """Show and edit settings."""
    session = get_session()

    profile = session.query(Profile).filter(Profile.name == "default").first()
    if not profile:
        profile = Profile(name="default")
        session.add(profile)
        session.commit()

    table = Table(title="Settings")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    table.add_column("Description", style="white")

    table.add_row("Kelly Fraction", f"{profile.kelly_fraction:.2f}", "Fraction of Kelly stake to use")
    table.add_row("Min Edge %", f"{profile.min_edge_pct:.1f}", "Minimum edge for value bets")
    table.add_row("Min Arb %", f"{profile.min_arb_pct:.1f}", "Minimum profit for arbitrage")
    table.add_row("Max Stake %", f"{profile.max_stake_pct:.1f}", "Max % of bankroll per bet")

    session.close()
    console.print(table)


def interactive_loop():
    """Main interactive loop."""
    show_banner()
    init_db()

    commands = {
        "extract": "Run extraction pipeline",
        "stats": "Show database statistics",
        "arbs": "Show arbitrage opportunities",
        "value": "Show value bets",
        "providers": "List configured providers",
        "settings": "Show settings",
        "help": "Show this help",
        "quit": "Exit application",
    }

    while True:
        console.print("\n[dim]Commands: extract, stats, arbs, value, providers, settings, help, quit[/dim]")
        cmd = Prompt.ask("[bold cyan]oddopp[/bold cyan]").lower().strip()

        if cmd == "quit" or cmd == "exit" or cmd == "q":
            console.print("[yellow]Goodbye![/yellow]")
            break
        elif cmd == "help":
            table = Table(title="Commands")
            table.add_column("Command", style="cyan")
            table.add_column("Description", style="white")
            for c, desc in commands.items():
                table.add_row(c, desc)
            console.print(table)
        elif cmd == "stats":
            show_stats()
        elif cmd == "arbs":
            show_arbitrage()
        elif cmd == "value":
            show_value_bets()
        elif cmd == "providers":
            show_providers()
        elif cmd == "settings":
            show_settings()
        elif cmd.startswith("extract"):
            parts = cmd.split()
            providers = parts[1:] if len(parts) > 1 else None
            skip_poly = "--no-poly" in parts
            if skip_poly and providers:
                providers = [p for p in providers if p != "--no-poly"]

            asyncio.run(run_extraction(providers or None, skip_poly))
        else:
            console.print(f"[red]Unknown command: {cmd}[/red]")
            console.print("[dim]Type 'help' for available commands[/dim]")


@app.command()
def run():
    """Start the interactive terminal application."""
    interactive_loop()


@app.command()
def extract(
    providers: Optional[list[str]] = typer.Argument(None, help="Providers to extract from"),
    no_poly: bool = typer.Option(False, "--no-poly", help="Skip Polymarket extraction"),
):
    """Run extraction without interactive mode."""
    asyncio.run(run_extraction(providers, no_poly))


@app.command()
def stats():
    """Show database statistics."""
    init_db()
    show_stats()


@app.command()
def arbs():
    """Show arbitrage opportunities."""
    init_db()
    show_arbitrage()


@app.command()
def value():
    """Show value betting opportunities."""
    init_db()
    show_value_bets()


if __name__ == "__main__":
    app()
