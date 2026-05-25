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

registry_group = cfg.OptGroup(
    name="registry",
    title="Service Registry settings",
)

registry_opts = [
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
    cfg.StrOpt(
        "aprsd_config_path",
        default="/config/aprsd.conf",
        help="Path to APRSD configuration file for health checks.",
    ),
    cfg.BoolOpt(
        "health_check_enabled",
        default=False,
        help="Enable background health checks for services.",
    ),
    cfg.IntOpt(
        "health_check_timeout",
        default=60,
        help="Seconds to wait for health check response.",
    ),
    cfg.StrOpt(
        "admin_username",
        default="admin",
        help="Username for admin interface.",
    ),
    cfg.StrOpt(
        "admin_password",
        default="",
        help="Password for admin interface. If empty, admin is disabled.",
    ),
    cfg.StrOpt(
        "db_path",
        default="",
        help="Path to SQLite database file. If set, uses SQLite as the storage backend. "
        "Example: /config/registry.db",
    ),
    cfg.BoolOpt(
        "git_backup_enabled",
        default=False,
        help="(Deprecated) Enable git-backed JSON storage. Use db_path instead.",
    ),
    cfg.StrOpt(
        "git_backup_path",
        default=f"{DEFAULT_CONFIG_DIR}/backup",
        help="(Deprecated) Path to the git repository for backups.",
    ),
    cfg.StrOpt(
        "git_backup_remote",
        default="",
        help="(Deprecated) Git remote URL for offsite backup.",
    ),
    cfg.IntOpt(
        "git_backup_push_interval",
        default=60,
        help="(Deprecated) Minutes between pushes to remote.",
    ),
    cfg.BoolOpt(
        "bulletin_enabled",
        default=False,
        help="Enable periodic APRS bulletin announcements.",
    ),
    cfg.IntOpt(
        "bulletin_interval",
        default=3600,
        help="Seconds between bulletin re-sends (default: 3600 = 1 hour).",
    ),
    cfg.ListOpt(
        "bulletin_messages",
        default=[
            "APRS Service Registry - aprs.hemna.com - by WB4BOR",
            "Find APRS services & commands. API: aprs.hemna.com/docs",
            "aprs.hemna.com - {count} services registered",
        ],
        help="Bulletin message lines (max 67 chars each). Each becomes BLN1, BLN2, etc. "
        "Supports {count} placeholder for current service count.",
    ),
]


def register_opts(config):
    config.register_group(registry_group)
    config.register_opts(registry_opts, group=registry_group)


def list_opts():
    return {
        "DEFAULT": [],
        registry_group.name: registry_opts,
    }
