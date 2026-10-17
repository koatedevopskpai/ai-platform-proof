import json

import pytest
from fastapi.testclient import TestClient

from app.core.telemetry import span
from app.main import app
from app.services.retrieval import RetrievalService


def test_nested_error_spans_correlate_and_do_not_log_content(capsys):
    with pytest.raises(RuntimeError):
        with span('request', 'fixture', trace_id='a'*32) as parent:
            with span('retrieval', 'fixture-db') as child:
                child['document'] = 'private-document-canary'
                raise RuntimeError('private-exception-canary')
    output = capsys.readouterr().out
    rows = [json.loads(line) for line in output.splitlines()]
    assert len(rows) == 2
    assert rows[0]['parent_span_id'] == parent['span_id']
    assert all(row['trace_id'] == 'a'*32 and row['status'] == 'error' for row in rows)
    assert 'private-document-canary' not in output
    assert 'private-exception-canary' not in output
    assert all(row['duration_ms'] >= 0 for row in rows)


def test_invalid_parent_identifier_is_not_logged(capsys):
    with span('fixture', 'fixture', parent_span_id='private-parent-canary'):
        pass
    output = capsys.readouterr().out
    assert 'private-parent-canary' not in output
    assert json.loads(output)['parent_span_id'] is None


def test_database_error_is_sanitized_in_http_response_and_logs(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError('private-document-canary')
    monkeypatch.setattr(RetrievalService, 'search', fail)
    client = TestClient(app)
    result = client.post('/rag', json={'query': 'private-query-canary'}, headers={'x-trace-id': 'b'*32})
    assert result.status_code == 503
    assert result.json() == {'error': 'service_unavailable', 'trace_id': 'b'*32}
    output = capsys.readouterr().out
    assert 'private-document-canary' not in output
    assert 'private-query-canary' not in output
    assert result.headers['x-trace-id'] == 'b'*32
