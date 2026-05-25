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


def main():
    cli(auto_envvar_prefix="APRS_SERVICE_REGISTRY")


if __name__ == "__main__":
    main()
