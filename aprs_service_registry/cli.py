import logging

import click
from loguru import logger
from oslo_config import cfg
from uvicorn import Config, Server

import aprs_service_registry
from aprs_service_registry import cli_helper, log
from aprs_service_registry import main as registry_main


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

LOG = logger
CONF = cfg.CONF


@click.group(cls=cli_helper.AliasedGroup, context_settings=CONTEXT_SETTINGS)
@click.version_option()
@click.pass_context
def cli(ctx):
    pass


@cli.command()
@cli_helper.add_options(cli_helper.common_options)
@click.pass_context
@cli_helper.process_standard_options
def server(ctx):
    """Start the aprs service registry server gateway process."""
    # Dump all the config options now.
    CONF.log_opt_values(LOG, logging.DEBUG)
    LOG.info(
        f"APRS Service Registry Started version: {aprs_service_registry.__version__}"
    )
    registry_main.APRSServices().load()

    server = Server(
        Config(
            "aprs_service_registry.main:app",
            host=CONF.registry.web_ip,
            port=CONF.registry.web_port,
            reload=True,
            log_level=logging.DEBUG,
        ),
    )
    log.setup_logging()
    server.run()
    LOG.info("APRS Service Registry Stopped")
    registry_main.APRSServices().save()


@cli.command()
@click.pass_context
def version(ctx):
    """Show the APRS Service Registry version."""
    click.echo(click.style("APRS Service Registry Version : ", fg="white"), nl=False)
    click.secho(f"{aprs_service_registry.__version__}", fg="yellow", bold=True)


@cli.command()
@cli_helper.add_options(cli_helper.common_options)
@click.pass_context
@cli_helper.process_standard_options
def seed(ctx):
    """Seed the registry with known APRS services."""
    from aprs_service_registry.main import APRSServices, registryRequest

    services = APRSServices()
    services.load()

    known_services = [
        {
            "callsign": "FIND",
            "description": (
                "APRS station lookup service. Send a callsign to retrieve "
                "last heard time, IGate station, speed, distance, and grid square."
            ),
            "service_website": "https://aprs.wiki/find/",
            "software": "aprsd",
            "callsign_owner": None,
            "status": "active",
            "health_check_command": None,
            "commands": [
                {
                    "name": "CALL-SSID",
                    "description": (
                        "Look up a station by callsign (e.g., K7TME-9). "
                        "Returns last heard, IGate, speed, distance, grid square."
                    ),
                },
            ],
            "featured": False,
        },
    ]

    added = 0
    for svc_data in known_services:
        callsign = svc_data["callsign"]
        if callsign in services.data:
            click.echo(f"  {callsign} already exists, skipping.")
            continue
        service = registryRequest(**svc_data)
        services.add_and_persist(callsign, service)
        click.echo(f"  {callsign} registered.")
        added += 1

    click.echo(f"\nDone. Added {added} service(s), {len(known_services) - added} skipped.")


@cli.command("migrate-to-sqlite")
@cli_helper.add_options(cli_helper.common_options)
@click.option("--db-path", default=None, help="Path for SQLite DB (overrides config)")
@click.pass_context
@cli_helper.process_standard_options
def migrate_to_sqlite(ctx, db_path):
    """Migrate data from pickle files to SQLite database."""
    import pickle
    from pathlib import Path

    from aprs_service_registry.db import RegistryDB

    save_location = CONF.registry.save_location
    target_db = db_path or CONF.registry.db_path
    if not target_db:
        target_db = f"{save_location}/registry.db"

    click.echo(f"Migrating pickle data to SQLite: {target_db}")
    click.echo(f"Reading pickle files from: {save_location}")
    click.echo()

    db = RegistryDB(target_db)

    # --- Migrate services ---
    services_file = Path(save_location) / "aprsservices.p"
    services_count = 0
    commands_count = 0
    if services_file.exists():
        with open(services_file, "rb") as fp:
            raw = pickle.load(fp)

        click.echo(f"Found {len(raw)} services in pickle file")
        for callsign, service in raw.items():
            # Convert Pydantic model to dict
            if hasattr(service, "model_dump"):
                svc_data = service.model_dump()
            elif hasattr(service, "dict"):
                svc_data = service.dict()
            elif isinstance(service, dict):
                svc_data = service
            else:
                svc_data = service.__dict__

            # Extract commands separately
            commands = svc_data.pop("commands", []) or []
            svc_data.pop("callsign", None)  # Key is the dict key

            db.upsert_service(
                callsign,
                {**svc_data, "commands": commands},
                actor=("system", "migration"),
            )
            services_count += 1
            commands_count += len(commands)

        click.secho(
            f"  Migrated {services_count} services, {commands_count} commands",
            fg="green",
        )
    else:
        click.echo("  No services pickle file found, skipping.")

    # --- Migrate health checks ---
    health_file = Path(save_location) / "healthchecks.p"
    health_count = 0
    if health_file.exists():
        with open(health_file, "rb") as fp:
            raw = pickle.load(fp)

        click.echo(f"Found health check data for {len(raw)} services")
        for callsign, results in raw.items():
            if not isinstance(results, list):
                continue
            for result in results:
                if hasattr(result, "__dict__"):
                    r = result.__dict__
                elif isinstance(result, dict):
                    r = result
                else:
                    continue

                # Normalize timestamp
                ts = r.get("timestamp")
                if hasattr(ts, "isoformat"):
                    ts = ts.isoformat()
                elif ts is None:
                    from datetime import datetime, timezone
                    ts = datetime.now(timezone.utc).isoformat()

                db.add_health_check(callsign, {
                    "timestamp": ts,
                    "success": bool(r.get("success", False)),
                    "response_time_ms": r.get("response_time_ms"),
                    "response_text": r.get("response_text"),
                    "error": r.get("error"),
                })
                health_count += 1

        click.secho(f"  Migrated {health_count} health check records", fg="green")
    else:
        click.echo("  No health checks pickle file found, skipping.")

    # --- Migrate pending commands ---
    pending_file = Path(save_location) / "pending_commands.p"
    pending_count = 0
    if pending_file.exists():
        with open(pending_file, "rb") as fp:
            raw = pickle.load(fp)

        click.echo(f"Found {len(raw)} pending command submissions")
        for id, pending in raw.items():
            if hasattr(pending, "__dict__"):
                p = pending.__dict__
            elif isinstance(pending, dict):
                p = pending
            else:
                continue

            ts = p.get("submitted_at")
            if hasattr(ts, "isoformat"):
                ts = ts.isoformat()

            db.submit_command({
                "id": p.get("id", str(id)),
                "callsign": p.get("callsign", ""),
                "command_name": p.get("command_name", ""),
                "command_description": p.get("command_description", ""),
                "submitted_at": ts,
                "submitted_by": p.get("submitted_by"),
            })
            pending_count += 1

        click.secho(f"  Migrated {pending_count} pending submissions", fg="green")
    else:
        click.echo("  No pending commands pickle file found, skipping.")

    # --- Summary ---
    click.echo()
    click.secho("Migration complete!", fg="green", bold=True)
    click.echo(f"  Database: {target_db}")
    counts = db.service_count()
    click.echo(f"  Services: {counts.get('total', 0)}")
    click.echo(f"  Health checks: {health_count}")
    click.echo(f"  Pending submissions: {pending_count}")
    click.echo()
    click.echo("To use SQLite, add to your registry.conf:")
    click.echo(f"  db_path = {target_db}")


def main():
    cli(auto_envvar_prefix="APRS_SERVICE_REGISTRY")


if __name__ == "__main__":
    main()
