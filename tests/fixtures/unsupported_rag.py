"""Deliberately wrong application implementation; used only in an isolated drill image."""


class UnsupportedRagService:
    def answer(self, query, contexts):
        return {'answer': 'Refunds are never permitted and all meal limits are unlimited.',
                'sources': [c['id'] for c in contexts], 'fallback': True, 'blocked': False}


rag_service = UnsupportedRagService()
