import asyncio
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi_socketio import SocketManager

from . import binding, globals, vue
from .client import Client
from .error import error_content
from .favicon import create_favicon_routes
from .helpers import safe_invoke
from .page import page
from .task_logger import create_task

globals.app = app = FastAPI()
globals.sio = sio = SocketManager(app=app)._sio

app.add_middleware(GZipMiddleware)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / 'static'), name='static')

globals.index_client = Client(page('/'), shared=True).__enter__()


@app.get('/')
def index():
    return globals.index_client.build_response()


@app.get('/_vue/dependencies/{path:path}')
def vue_dependencies(path: str):
    return FileResponse(path, media_type='text/javascript')


@app.get('/_vue/components/{name}')
def vue_dependencies(name: str):
    return FileResponse(vue.js_components[name], media_type='text/javascript')


@app.on_event('startup')
def on_startup() -> None:
    globals.state = globals.State.STARTING
    globals.loop = asyncio.get_running_loop()
    create_favicon_routes()
    [safe_invoke(t) for t in globals.startup_handlers]
    create_task(binding.loop())
    globals.state = globals.State.STARTED
    print(f'NiceGUI ready to go on http://{globals.host}:{globals.port}')


@app.on_event('shutdown')
def shutdown() -> None:
    globals.state = globals.State.STOPPING
    [safe_invoke(t) for t in globals.shutdown_handlers]
    [t.cancel() for t in globals.tasks]
    globals.state = globals.State.STOPPED


@app.exception_handler(404)
async def exception_handler(_: Request, exc: Exception):
    with Client(page('')) as client:
        error_content(404, str(exc))
    return client.build_response()


@app.exception_handler(Exception)
async def exception_handler(_: Request, exc: Exception):
    with Client(page('')) as client:
        error_content(500, str(exc))
    return client.build_response()


@sio.on('connect')
async def handle_connect(sid: str, _) -> None:
    client = get_client(sid)
    if not client:
        return
    client.environ = sio.get_environ(sid)
    sio.enter_room(sid, str(client.id))


@sio.on('disconnect')
async def handle_disconnect(sid: str) -> None:
    client = get_client(sid)
    if not client:
        return
    if not client.shared:
        del globals.clients[client.id]


@sio.on('event')
def handle_event(sid: str, msg: Dict) -> None:
    client = get_client(sid)
    if not client:
        return
    with client:
        sender = client.elements.get(msg['id'])
        if sender:
            sender.handle_event(msg)


@sio.on('javascript_response')
def handle_event(sid: str, msg: Dict) -> None:
    client = get_client(sid)
    if not client:
        return
    client.waiting_javascript_commands[msg['request_id']] = msg['result']


def get_client(sid: str) -> Optional[Client]:
    query_bytes: bytearray = sio.get_environ(sid)['asgi.scope']['query_string']
    query = urllib.parse.parse_qs(query_bytes.decode())
    client_id = int(query['client_id'][0])
    return globals.clients.get(client_id)
