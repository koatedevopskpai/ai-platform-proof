from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import router
from app.core.db import Base, engine
from app.core.telemetry import span


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="ai-platform rag-api", version="0.1.0", lifespan=lifespan)


@app.middleware('http')
async def trace_request(request, call_next):
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
