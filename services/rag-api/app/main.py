from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import router
from app.core.db import Base, engine
from app.core.telemetry import span
from app.core import tokens


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.connect() as conn:
        # Serialise concurrent startup. CREATE EXTENSION IF NOT EXISTS is not atomic against
        # concurrent DDL: two instances starting on a fresh database race and one fails with a
        # duplicate-key error on pg_extension. This lock is a mitigation, not a fix — the durable
        # answer is a single-writer migration step. See docs/architecture.md §4.
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('aiplatform-schema-init'))"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="ai-platform rag-api", version="0.1.0", lifespan=lifespan)


@app.middleware('http')
async def trace_request(request, call_next):
    # Attribute any model spend caused by this request to the route that caused it.
    tokens.set_endpoint(request.url.path)
    with span('http.request', 'rag-api', trace_id=request.headers.get('x-trace-id'),
              parent_span_id=request.headers.get('x-parent-span-id')) as observation:
        try:
            response = await call_next(request)
        except Exception as exc:
            observation.update(status='error', error_type=type(exc).__name__)
            response = JSONResponse({'error': 'service_unavailable', 'trace_id': observation['trace_id']}, status_code=503)
        observation['http_status'] = response.status_code
        if response.status_code >= 400:
            observation['status'] = 'error'
        response.headers['x-trace-id'] = observation['trace_id']
        return response


app.include_router(router)
