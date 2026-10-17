import httpx

from app.core.config import settings
from app.services.rag import RAGService


def configure(monkeypatch, response):
    monkeypatch.setattr(settings, 'openai_api_key', 'synthetic-fixture-only')
    monkeypatch.setattr(settings, 'openai_base_url', 'http://fixture-provider/v1')
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: response)


def test_http_503_fallback_preserves_source_and_reports_reason(monkeypatch):
    response = httpx.Response(503, request=httpx.Request('POST', 'http://fixture-provider/v1/chat/completions'))
    configure(monkeypatch, response)
    result = RAGService().answer('What is the refund window?', [{'id': 'fixture', 'content': 'Refunds within 30 days.'}])
    assert result['fallback'] is True
    assert result['fallback_reason'] == 'provider_http_503'
    assert result['generation_mode'] == 'deterministic_fallback'
    assert '30 days' in result['answer']
    assert result['sources'] == ['fixture']


def test_empty_provider_response_is_not_success(monkeypatch):
    response = httpx.Response(200, json={'choices': [{'message': {'content': ''}}]},
                              request=httpx.Request('POST', 'http://fixture-provider/v1/chat/completions'))
    configure(monkeypatch, response)
    result = RAGService().answer('refund?', [{'id': 'fixture', 'content': 'Refunds within 30 days.'}])
    assert result['fallback'] is True
    assert result['fallback_reason'] == 'provider_invalid_response'


def test_transport_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(settings, 'openai_api_key', 'synthetic-fixture-only')
    def fail(*args, **kwargs):
        raise httpx.ConnectError('synthetic fixture')
    monkeypatch.setattr(httpx, 'post', fail)
    result = RAGService().answer('refund?', [{'id': 'fixture', 'content': 'Refunds within 30 days.'}])
    assert result['fallback_reason'] == 'provider_transport_error'
