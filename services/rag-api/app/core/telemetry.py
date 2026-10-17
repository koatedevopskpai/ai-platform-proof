"""Local structured spans; metadata only, no prompts/documents/provider errors."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import re
import time
import uuid

_context = ContextVar('trace_context', default=None)


def identifier(value, width):
    return value if isinstance(value, str) and re.fullmatch('[0-9a-f]{'+str(width)+'}', value) else uuid.uuid4().hex[:width]


@contextmanager
def span(operation, component, trace_id=None, parent_span_id=None):
    parent = _context.get()
    trace = parent['trace_id'] if parent else identifier(trace_id, 32)
    entry = {'event': 'span', 'trace_id': trace, 'span_id': uuid.uuid4().hex[:16],
             'parent_span_id': parent['span_id'] if parent else
                 (parent_span_id if isinstance(parent_span_id, str) and re.fullmatch('[0-9a-f]{16}', parent_span_id) else None),
             'component': component, 'operation': operation, 'status': 'ok',
             'started_at_utc': datetime.now(timezone.utc).isoformat()}
    token = _context.set(entry)
    started = time.perf_counter()
    try:
        yield entry
    except Exception as exc:
        entry.update(status='error', error_type=type(exc).__name__)
        raise
    finally:
        entry['duration_ms'] = round((time.perf_counter()-started)*1000, 3)
        # Emit only explicitly allowlisted attributes; exception messages never enter logs.
        allowed = {'event','trace_id','span_id','parent_span_id','component','operation','status',
                   'started_at_utc','duration_ms','error_type','http_status','generation_mode','fallback_reason'}
        print(json.dumps({k:v for k,v in entry.items() if k in allowed}), flush=True)
        _context.reset(token)
