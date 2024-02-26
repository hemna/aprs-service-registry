from fastapi import FastAPI, WebSocket, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import json
import logging
from oslo_config import cfg
from pydantic import BaseModel
import time
import threading
import wrapt

from aprs_service_registry import conf, utils  # noqa
from aprs_service_registry import objectstore


LOG = logging.getLogger(__name__)
CONF = cfg.CONF

app = FastAPI()
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")


class registryRequest(BaseModel):
    """Request to register a service with the registry."""
    callsign: str
    description: str
    service_website: str
    uptime: str


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
        del self.data[callsign]




@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    LOG.debug("PISS")
    services_str = ""
    services = APRSServices()
    for service in services:
        services_str += f"{service} - {services[service].description}\n"

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request,
                 "services": services_str}
    )


@app.post("/api/v1/registry", response_class=JSONResponse)
async def registry(request: registryRequest):
    """Register a service with the registry and/or update uptime."""
    LOG.info(f"registry: {request}")
    services = APRSServices()
    services.add(request.callsign, request)
    for service in services:
        LOG.info(f"{service}: {services[service].description} - {services[service].service_website} - {services[service].uptime}")
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