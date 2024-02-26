import logging
from pathlib import Path

from oslo_config import cfg


home = str(Path.home())
DEFAULT_CONFIG_DIR = f"{home}/.config/aprs_service_registry/"
DEFAULT_MAGIC_WORD = "CHANGEME!!!"

LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

main_opts = [
    cfg.BoolOpt(
        "enable_save",
        default=True,
        help="Enable saving of watch list, packet tracker between restarts.",
    ),
    cfg.StrOpt(
        "save_location",
        default=DEFAULT_CONFIG_DIR,
        help="Save location for packet tracking files.",
    ),
    cfg.BoolOpt(
        "trace_enabled",
        default=False,
        help="Enable code tracing",
    ),
    cfg.IPOpt(
        "web_ip",
        default="0.0.0.0",
        help="The ip address to listen on",
    ),
    cfg.PortOpt(
        "web_port",
        default=8001,
        help="The port to listen on",
    ),
    cfg.StrOpt(
        "log_level",
        default="INFO",
        choices=LOG_LEVELS.keys(),
        help="Log level for logging of events.",
    ),
]


def register_opts(config):
    config.register_opts(main_opts)


def list_opts():
    return {
        "DEFAULT": main_opts,
    }
