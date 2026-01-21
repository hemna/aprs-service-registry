import asyncio
from datetime import datetime
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_utils.tasks import repeat_every
import json
import logging
from oslo_config import cfg
from pydantic import BaseModel, Field
import time
import threading
import wrapt
from typing import List

from aprsd.clients import aprsis
from aprsd.threads import aprsd as aprsd_threads
from aprsd.packets import core as aprsd_packets

from aprs_service_registry import conf, utils  # noqa
from aprs_service_registry import objectstore


LOG = logging.getLogger(__name__)
CONF = cfg.CONF

APRSIS_USER = "REGISTRY"
APRSIS_PASS = "26595"
APRSIS_HOST = "noam.aprs2.net"
APRSIS_PORT = 14580

app = FastAPI()
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")

APRSIS_CLIENT = None

class serviceCheck(BaseModel):
    """Check the status of a service."""
    up: bool = Field(default=True, description="Is the service up?")
    time: datetime = Field(default=datetime.now(), description="The time of the check.")


class registryRequest(BaseModel):
    """Request to register a service with the registry."""
    callsign: str = Field(frozen=True, description="The callsign of the service.")
    description: str = Field(description="A description of the service.")
    service_website: str = Field(description="The website of the service.")
    software: str = Field(description="The software the service is running.")
    checks: List[serviceCheck] = Field(default=[], description="The checks of the service.")

    def add_check(self, check: serviceCheck):
        self.checks.append(check)



class APRSServices(objectstore.ObjectStoreMixin):
    _instance = None
    lock = threading.Lock()
    data: dict = {}

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_store()
            cls._instance.data = {}
        return cls._instance

    @wrapt.synchronized(lock)
    def __getitem__(self, callsign):
        return self.data[callsign]

    @wrapt.synchronized(lock)
    def add(self, callsign, data: registryRequest):
        self.data[callsign] = data

    @wrapt.synchronized(lock)
    def remove(self, callsign):
        if callsign in self.data:
            del self.data[callsign]


class APRSConsumeThread(aprsd_threads.APRSDThread):
    def __init__(self):
        self.thread_stop = False
        super().__init__(name="APRSConsumeThread")

    def process_packet(self, packet):
        LOG.debug(f"GOT packet: {packet}")
        pkt = aprsd_packets.Packet.factory(packet)
        LOG.debug(pkt)
        pkt.log(header="RX")
        return

    def loop(self):
        try:
            APRSIS_CLIENT.consumer(self.process_packet, blocking=False)
        except Exception as e:
            LOG.error(f"Error in consume thread: {e}")
        time.sleep(1)
        return True


@app.on_event("startup")
@repeat_every(seconds=60)
def save_services(*args, **kwargs):
    LOG.debug("Saving services")
    APRSServices().save()


@app.on_event("shutdown")
def shutdown_event():
    LOG.debug("Shut down event")
    APRSServices().save()
    aprsd_threads.APRSDThreadList().stop_all()


@app.on_event("startup")
def _start_aprsis_client():
    global APRSIS_CLIENT
    if not APRSIS_CLIENT:
        APRSIS_CLIENT = _build_aprs_client()
    consume_thread = APRSConsumeThread()
    consume_thread.start()


def _build_aprs_client():
    LOG.info("Building APRS Client")
    aprs_client = aprsis.Aprsdis(
        APRSIS_USER,
        passwd=APRSIS_PASS,
        host=APRSIS_HOST,
        port=APRSIS_PORT)
    # Force the log to be the same
    aprs_client.logger = LOG
    aprs_client.connect()
    connected = True
    return aprs_client


@app.on_event("startup")
@repeat_every(wait_first=1, seconds=20, logger=LOG)
def check_services_alive():
    LOG.debug("Checking services")
    services = APRSServices()
    for service in services:
        LOG.debug(f"Checking {service} - {services[service].description}")
        # do some check to see if the service is alive
        # if not, remove it from the registry
        # if it is, update the uptime
        pkt = aprsd_packets.MessagePacket(
            from_call=APRSIS_USER,
            to_call=service,
            message_text="ping",
        )
        pkt.prepare()
        pkt.log(header="TX")
        LOG.info(f"Sending {pkt}")
        APRSIS_CLIENT.send(pkt)
        time.sleep(1)



@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get(request: Request):
    """Render the index page."""
    services = APRSServices()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request,
                 "services": services}
    )


@app.post("/api/v1/registry", response_class=JSONResponse)
async def registry(request: registryRequest):
    """Register a service with the registry and/or update."""
    LOG.info(f"registry: {request}")
    services = APRSServices()
    request.callsign = request.callsign.upper()
    services.add(request.callsign.upper(), request)
    for service in services:
        LOG.info(f"{service}: {services[service].description} - {services[service].service_website}")
    return json.dumps({"status": "ok"})


@app.delete("/api/v1/registry/{callsign}", response_class=JSONResponse)
async def registry_delete(callsign: str):
    """Remove a service from the registry."""
    services = APRSServices()
    LOG.info(f"Removing {callsign} from the registry.")
    services.remove(callsign.upper())
    return json.dumps({"status": "ok"})


async def ws_process_balls(msg):
    time.sleep(2)
    return {"call": "balls", "data": msg["message"]}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    LOG.error('CONNECTING...')
    await websocket.accept()
    while True:
        try:
            msg = await websocket.receive_json()
            LOG.info(f"msg = {msg['message']}")
            resp = await ws_process_balls(msg)
            await websocket.send_json(resp)
        except Exception as e:
            print(e)
            break
    LOG.debug("CONNECTION DEAD...")
