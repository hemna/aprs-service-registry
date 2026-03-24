=====================
APRS Service Registry
=====================

APRS Service Registry is an API and website for registering and discovering APRS services.
This is useful for HAM Radio operators to have a centralised place to find services that are available to them.

The main service runs at https://aprs.hemna.com and is open to all to use.

Running the Application
=======================

Prerequisites
-------------

- **Python 3.11** or newer
- **uv** (recommended, for the Makefile) — `curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`

If you do not use the Makefile, you can use ``pip`` instead of ``uv``.

Quick start (with Make and uv)
------------------------------

1. **Install dependencies and create the virtual environment:**

   .. code-block:: bash

      make update-requirements   # generate requirements.txt and dev-requirements.txt (first time only)
      make run                   # or "make dev" for development, including test/lint tools

2. **Start the server:**

   .. code-block:: bash

      make server

3. **Open in a browser:** http://localhost:8001

   - Web UI: http://localhost:8001/
   - API docs: http://localhost:8001/docs

Run without Make
----------------

.. code-block:: bash

   uv venv .venv
   source .venv/bin/activate   # or: .venv\Scripts\activate on Windows
   uv pip install -e .
   uv pip install -r requirements.txt
   aprs-service-registry server

Or with pip:

.. code-block:: bash

   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   pip install -r requirements.txt
   aprs-service-registry server

Docker
------

Build and run with Docker (uses **tini** as init, runs as non-root):

.. code-block:: bash

   docker build -t aprs-service-registry .
   docker run -p 8001:80 aprs-service-registry

- **Web UI:** http://localhost:8001/
- **API docs:** http://localhost:8001/docs

To override the config, mount your own:

.. code-block:: bash

   docker run -p 8001:80 -v /path/to/registry.conf:/app/config/registry.conf:ro aprs-service-registry

To persist registered services, mount a volume for the data directory:

.. code-block:: bash

   docker run -p 8001:80 \
     -v aprs-registry-data:/home/app/.config/aprs_service_registry \
     -v /path/to/registry.conf:/app/config/registry.conf:ro \
     aprs-service-registry

Docker Compose
--------------

The easiest way to run with Docker Compose:

.. code-block:: bash

   # Copy the example config (optional)
   cp config/registry.conf.example config/registry.conf
   # Edit config/registry.conf if needed

   # Build and start
   docker-compose up -d

   # View logs
   docker-compose logs -f

   # Stop
   docker-compose down

The ``docker-compose.yml`` includes:
- Port mapping: host 8001 -> container 80
- Config file mount: ``./config/registry.conf`` (optional, uses default if missing)
- Data persistence: named volume ``aprs-registry-data``
- Health checks and auto-restart

CLI commands
------------

- **aprs-service-registry server** — Start the web server (default: http://0.0.0.0:8001)
- **aprs-service-registry version** — Show the version

Use ``-c``/``--config`` to point to a config file:

.. code-block:: bash

   aprs-service-registry server -c /path/to/registry.conf

Configuration
-------------

The server looks for a config file at ``~/.config/aprs_service_registry/registry.conf`` by default. If the file does not exist, create it. A minimal example:

.. code-block:: ini

   [registry]
   # Listen address (default: 0.0.0.0)
   web_ip = 0.0.0.0
   # Port (default: 8001)
   web_port = 8001
   # Persist registered services to disk (default: true)
   enable_save = true
   # Directory for save file and config (default: ~/.config/aprs_service_registry/)
   save_location = /home/you/.config/aprs_service_registry/
   # Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
   log_level = INFO

All options under ``[registry]`` have defaults; you can use an empty file or only override what you need.

Make targets
------------

- **make run** — Create venv and install runtime dependencies (requirements.txt + the app)
- **make dev** — Create venv and install runtime + dev dependencies (requirements.txt, dev-requirements.txt, app)
- **make server** — Start the server (depends on ``run``)
- **make update-requirements** — Regenerate requirements.txt and dev-requirements.txt from requirements.in and dev-requirements.in (requires uv)
- **make test** — Run tests
- **make help** — List available targets

What is an APRS Service?
========================

An APRS Service is any service that is available to HAM Radio operators that is useful to them.
Services typically are automated responders, or services that provide information to HAM Radio operators
on a particular callsign over the APRS network.

Common services include:

- **SMSGTE** — A service that allows HAM Radio operators to send and receive SMS messages over the APRS network.

API Reference
=============

The service provides a REST API for managing registered services. Full API documentation is available at http://localhost:8001/docs (when the server is running).

List All Services
-----------------

**GET** ``/api/v1/registry``

Returns all registered services with metadata.

.. code-block:: bash

   curl https://aprs.hemna.com/api/v1/registry

Response:

.. code-block:: json

   {
     "count": 2,
     "timestamp": "2026-03-24T01:30:00Z",
     "services": [
       {
         "callsign": "SMSGTE",
         "description": "SMS Gateway Service",
         "service_website": "https://smsgte.example.com",
         "software": "aprsd 3.0",
         "callsign_owner": "N0CALL"
       }
     ]
   }

Get a Single Service
--------------------

**GET** ``/api/v1/registry/{callsign}``

Returns a single service by callsign (case-insensitive).

.. code-block:: bash

   curl https://aprs.hemna.com/api/v1/registry/SMSGTE

Response (200 OK):

.. code-block:: json

   {
     "callsign": "SMSGTE",
     "description": "SMS Gateway Service",
     "service_website": "https://smsgte.example.com",
     "software": "aprsd 3.0",
     "callsign_owner": "N0CALL"
   }

Response (404 Not Found):

.. code-block:: json

   {
     "detail": "Service 'NOTFOUND' not found"
   }

Register or Update a Service
----------------------------

**POST** ``/api/v1/registry``

Register a new service or update an existing one.

.. code-block:: bash

   curl -X POST https://aprs.hemna.com/api/v1/registry \
     -H "Content-Type: application/json" \
     -d '{
       "callsign": "YOURCALL",
       "description": "Description of your service",
       "service_website": "https://your-service.example.com",
       "software": "YourSoftware version 1.0"
     }'

Delete a Service
----------------

**DELETE** ``/api/v1/registry/{callsign}``

Remove a service from the registry.

.. code-block:: bash

   curl -X DELETE https://aprs.hemna.com/api/v1/registry/YOURCALL

Service Status
--------------

Services have a status field that can be one of:

- **active** (default) — Service is operational
- **down** — Service is temporarily unavailable
- **deleted** — Service is soft-deleted

**Filtering by status:**

By default, ``GET /api/v1/registry`` returns only active services. Use query parameters to include other statuses:

.. code-block:: bash

   # Include down services (active + down)
   curl https://aprs.hemna.com/api/v1/registry?include_down=true

   # Include deleted services (active + deleted)
   curl https://aprs.hemna.com/api/v1/registry?include_deleted=true

   # Include all services
   curl https://aprs.hemna.com/api/v1/registry?include_all=true

**Setting service status:**

Include the ``status`` field when registering or updating a service:

.. code-block:: bash

   curl -X POST https://aprs.hemna.com/api/v1/registry \
     -H "Content-Type: application/json" \
     -d '{"callsign": "MYSERVICE", "description": "...", "service_website": "...", "software": "...", "status": "down"}'

**Soft delete:**

``DELETE /api/v1/registry/{callsign}`` sets the service status to ``deleted`` rather than removing it permanently. The service can still be fetched by callsign and will appear with ``?include_deleted=true``.

The web UI at http://localhost:8001/ also has an interactive "How to Register" section with examples (cURL, APRSD config, etc.).
