import logging
from oslo_config import cfg
from uvicorn import Config, Server

import click

import aprs_service_registry
from aprs_service_registry import conf, cli_helper, log
from aprs_service_registry import main as registry_main

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

LOG = logging.getLogger(__name__)
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
    LOG.info(f"APRS Service Registry Started version: {aprs_service_registry.__version__}")
    registry_main.APRSServices().load()

    server = Server(
        Config(
            "aprs_service_registry.main:app",
            host=CONF.web_ip,
            port=CONF.web_port,
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


def main():
    cli(auto_envvar_prefix="APRS_SERVICE_REGISTRY")


if __name__ == "__main__":
    main()
