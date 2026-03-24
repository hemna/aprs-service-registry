"""Pytest configuration and fixtures for APRS Service Registry tests."""

import pytest
from oslo_config import cfg

# Register configuration options before any tests run
from aprs_service_registry.conf import common


@pytest.fixture(scope="session", autouse=True)
def register_config():
    """Register config options once before all tests."""
    CONF = cfg.CONF
    # Only register if not already registered
    if "registry" not in CONF._groups:
        common.register_opts(CONF)
    # Set enable_save to False for tests to avoid file I/O
    CONF.set_override("enable_save", False, group="registry")
