import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .db import init_db
from .logging_setup import log_event, request_id_var, setup_logging
from .routes import render, router
from .seed import seed_demo_data

setup_logging()
log = logging.getLogger("meetinghub.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if config.SEED_DEMO_DATA:
        seed_demo_data()
    log_event(log, logging.INFO, "app.started", ai_provider=config.AI_PROVIDER,
              ai_configured=bool(config.AI_API_KEY), model=config.AI_MODEL)
    yield


app = FastAPI(title="MeetingHub", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("request.unhandled_error", extra={"ctx": {"method": request.method, "path": request.url.path}})
        response = render(request, "error.html", status_code=500, code=500, rid=rid,
                          message="Something went wrong on our side. Quote the request ID below if you report it.")
    response.headers["X-Request-ID"] = rid
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    log_event(log, level, "request", method=request.method, path=request.url.path, status=response.status_code,
              duration_ms=round((time.perf_counter() - started) * 1000))
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return render(request, "error.html", status_code=exc.status_code, code=exc.status_code, rid=request_id_var.get(),
                  message="That page does not exist." if exc.status_code == 404 else str(exc.detail))


@app.exception_handler(LookupError)
async def not_found(request: Request, exc: LookupError):
    return render(request, "error.html", status_code=404, code=404, rid=request_id_var.get(), message=str(exc))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return render(request, "error.html", status_code=422, code=422, rid=request_id_var.get(),
                  message="Some of the submitted values were not valid. Go back and check the form.")
