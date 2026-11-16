"""Embedding service: mode selection, requested width, and provider-response handling.

The width check exists because a provider returning 1536 dimensions against a ``vector(384)``
column fails at insert time as an opaque PostgreSQL type error, several layers from the cause.
These tests pin the failure to the place it happens.
"""
import httpx

from app.core.config import settings
from app.services.embedding import EmbeddingService


def configure(monkeypatch, *, mode="openai", send_dimensions=True, dim=384):
    monkeypatch.setattr(settings, 'embedding_mode', mode)
    monkeypatch.setattr(settings, 'openai_api_key', 'synthetic-fixture-only')
    monkeypatch.setattr(settings, 'openai_base_url', 'http://fixture-provider/v1')
    monkeypatch.setattr(settings, 'embedding_dim', dim)
    monkeypatch.setattr(settings, 'embedding_send_dimensions', send_dimensions)


def install_provider(monkeypatch, items):
    captured = {}

    def fake_post(self, url, json=None, headers=None, **kwargs):
        captured['url'] = url
        captured['json'] = json
        return httpx.Response(200, json={'data': items}, request=httpx.Request('POST', url))

    monkeypatch.setattr(httpx.Client, 'post', fake_post)
    return captured


def vector(fill, dim=384):
    return [fill] * dim


def test_mock_mode_is_deterministic_and_matches_schema_width():
    service = EmbeddingService()
    first = service.embed_texts(['refunds within 30 days'])[0]
    second = service.embed_texts(['refunds within 30 days'])[0]
    assert first == second
    assert len(first) == settings.embedding_dim


def test_mock_mode_differs_for_different_text():
    service = EmbeddingService()
    assert service.embed_texts(['refund policy'])[0] != service.embed_texts(['meal limit'])[0]


def test_dimensions_parameter_is_sent_so_the_schema_width_is_preserved(monkeypatch):
    configure(monkeypatch, send_dimensions=True)
    captured = install_provider(monkeypatch, [{'index': 0, 'embedding': vector(0.01)}])
    EmbeddingService().embed_texts(['refund policy'])
    assert captured['json']['dimensions'] == 384
    assert captured['url'].endswith('/embeddings')


def test_dimensions_parameter_can_be_disabled_for_providers_that_reject_it(monkeypatch):
    configure(monkeypatch, send_dimensions=False)
    captured = install_provider(monkeypatch, [{'index': 0, 'embedding': vector(0.01)}])
    EmbeddingService().embed_texts(['refund policy'])
    assert 'dimensions' not in captured['json']


def test_wrong_width_raises_at_the_provider_boundary(monkeypatch):
    configure(monkeypatch)
    install_provider(monkeypatch, [{'index': 0, 'embedding': [0.1] * 1536}])
    try:
        EmbeddingService().embed_texts(['refund policy'])
    except ValueError as exc:
        assert '1536' in str(exc) and '384' in str(exc)
    else:
        raise AssertionError('a wrong-width embedding was accepted')


def test_provider_batch_is_ordered_by_index_not_response_order(monkeypatch):
    configure(monkeypatch)
    install_provider(monkeypatch, [
        {'index': 1, 'embedding': vector(0.02)},
        {'index': 0, 'embedding': vector(0.01)},
    ])
    vectors = EmbeddingService().embed_texts(['first', 'second'])
    assert vectors[0][0] == 0.01
    assert vectors[1][0] == 0.02


def test_mock_mode_is_used_when_no_key_is_configured(monkeypatch):
    configure(monkeypatch, mode='openai')
    monkeypatch.setattr(settings, 'openai_api_key', '')
    install_provider(monkeypatch, [{'index': 0, 'embedding': vector(0.99)}])
    produced = EmbeddingService().embed_texts(['refund policy'])[0]
    assert len(produced) == settings.embedding_dim
    assert produced != vector(0.99)
