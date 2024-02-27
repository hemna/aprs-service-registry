import click
from functools import update_wrapper
import logging
from pathlib import Path
import typing as t
from oslo_config import cfg

import aprs_service_registry
from aprs_service_registry import conf  # noqa: F401
from aprs_service_registry import log
from aprs_service_registry.utils import trace


CONF = cfg.CONF
home = str(Path.home())
DEFAULT_CONFIG_DIR = f"{home}/.config/aprs_service_registry/"
DEFAULT_SAVE_FILE = f"{home}/.config/aprs_service_registry/aprsd.p"
DEFAULT_CONFIG_FILE = f"{home}/.config/aprs_service_registry/registry.conf"


F = t.TypeVar("F", bound=t.Callable[..., t.Any])

common_options = [
    click.option(
        "-c",
        "--config",
        "config_file",
        show_default=True,
        default=DEFAULT_CONFIG_FILE,
        help="The aprsd config file to use for options.",
    ),
]


class AliasedGroup(click.Group):
    def command(self, *args, **kwargs):
        """A shortcut decorator for declaring and attaching a command to
        the group.  This takes the same arguments as :func:`command` but
        immediately registers the created command with this instance by
        calling into :meth:`add_command`.
        Copied from `click` and extended for `aliases`.
        """
        def decorator(f):
            aliases = kwargs.pop('aliases', [])
            cmd = click.decorators.command(*args, **kwargs)(f)
            self.add_command(cmd)
            for alias in aliases:
                self.add_command(cmd, name=alias)
            return cmd
        return decorator

    def group(self, *args, **kwargs):
        """A shortcut decorator for declaring and attaching a group to
        the group.  This takes the same arguments as :func:`group` but
        immediately registers the created command with this instance by
        calling into :meth:`add_command`.
        Copied from `click` and extended for `aliases`.
        """
        def decorator(f):
            aliases = kwargs.pop('aliases', [])
            cmd = click.decorators.group(*args, **kwargs)(f)
            self.add_command(cmd)
            for alias in aliases:
                self.add_command(cmd, name=alias)
            return cmd
        return decorator


def add_options(options):
    def _add_options(func):
        for option in reversed(options):
            func = option(func)
        return func
    return _add_options


def process_standard_options(f: F) -> F:
    def new_func(*args, **kwargs):
        # Must annotate @click.pass_context
        # before @cli_helper.process_standard_options
        ctx = args[0]
        ctx.ensure_object(dict)
        config_file_found = True
        if kwargs["config_file"]:
            default_config_files = [kwargs["config_file"]]
        else:
            default_config_files = None
        try:
            CONF(
                [], project="aprs_service_registry", version=aprs_service_registry.__version__,
                default_config_files=default_config_files,
            )
        except cfg.ConfigFilesNotFoundError:
            config_file_found = False
        # ctx.obj["config_file"] = kwargs["config_file"]
        log.setup_logging()
        if CONF.trace_enabled:
            trace.setup_tracing(["method", "api"])

        if not config_file_found:
            LOG = logging.getLogger(__name__)   # noqa: N806
            LOG.error("No config file found!! run 'aprsd sample-config'")

        del kwargs["config_file"]
        return f(*args, **kwargs)

    return update_wrapper(t.cast(F, new_func), f)


def process_standard_options_no_config(f: F) -> F:
    """Use this as a decorator when config isn't needed."""
    def new_func(*args, **kwargs):
        ctx = args[0]
        ctx.ensure_object(dict)
        ctx.obj["loglevel"] = kwargs["loglevel"]
        ctx.obj["config_file"] = kwargs["config_file"]
        ctx.obj["quiet"] = kwargs["quiet"]
        log.setup_logging_no_config(
            ctx.obj["loglevel"],
            ctx.obj["quiet"],
        )

        del kwargs["loglevel"]
        del kwargs["config_file"]
        del kwargs["quiet"]
        return f(*args, **kwargs)

    return update_wrapper(t.cast(F, new_func), f)
