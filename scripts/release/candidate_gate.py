"""Three-case deterministic acceptance against actual HTTP candidate responses.

Uses E04 hard checks. Scores are fixture predicates, NOT LLM/semantic quality.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

from gate_policy import assess_rows, load_policy


def load_dataset(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema') != 1 or data.get('execution_mode') != 'synthetic_mock':
        raise ValueError('unsupported acceptance dataset mode/schema')
    docs, cases = data.get('documents'), data.get('cases')
    if not isinstance(docs, list) or not docs or not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        raise ValueError('missing documents or cases')
    ids = [d['id'] for d in docs]
    if len(set(ids)) != len(ids) or any(not isinstance(d['content'], str) or not d['content'].strip() for d in docs):
        raise ValueError('invalid corpus')
    case_ids = [c['id'] for c in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError('duplicate cases')
    for case in cases:
        if (not isinstance(case['question'], str) or not case['question'].strip()
                or case['source_id'] not in ids or not case.get('required_phrases')
                or any(not isinstance(p, str) or not p for p in case['required_phrases'])):
            raise ValueError('invalid acceptance case')
    return data


def post(base, route, payload):
    request = Request(base + route, data=json.dumps(payload).encode(),
                      headers={'content-type': 'application/json'}, method='POST')
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def evaluate(base, dataset_path, provenance, request=post, policy_path=None):
    dataset = load_dataset(dataset_path)
    corpus = {d['id']: d['content'] for d in dataset['documents']}
    for doc in dataset['documents']:
        ingestion = request(base, '/ingest', doc)
        if ingestion.get('id') != doc['id'] or ingestion.get('dimensions') != 384:
            raise ValueError('gateway ingestion did not confirm expected fixture/vector dimensions')
    rows = []
    for case in dataset['cases']:
        row = {'case_id': case['id'], 'question': case['question'], 'generated_answer': '',
               'retrieved_contexts': [], 'source_ids': [], 'faithfulness': None, 'answer_relevancy': None}
        try:
            query = {'query': case['question'], 'top_k': 3}
            retrieval = request(base, '/search', query)
            answer = request(base, '/rag', query)
            results = retrieval['results']
            ids = [r['id'] for r in results]
            contexts = [r['content'] for r in results]
            generated = answer['answer']
            if not isinstance(generated, str) or not generated.strip():
                raise ValueError('missing application answer')
            # Check actual response provenance and source bytes, not dataset-provided answers.
            valid_sources = (bool(ids) and len(ids) == len(set(ids)) and
                all(i in corpus and c == corpus[i] for i, c in zip(ids, contexts)) and
                answer['sources'] == ids and case['source_id'] in ids)
            # Exact fixture support: substantive answer tokens must exist in returned sources.
            # This intentionally simple predicate is not semantic entailment or a judge model.
            substantive = generated.removeprefix('Per the source material: ')
            tokens = set(re.findall(r'[a-z0-9]+', substantive.lower()))
            supported = bool(tokens) and tokens <= set(re.findall(r'[a-z0-9]+', ' '.join(contexts).lower()))
            accepted = (all(p.lower() in generated.lower() for p in case['required_phrases']) and
                        not any(p.lower() in generated.lower() for p in case.get('forbidden_phrases', [])))
            explicit_mock = answer.get('fallback') is True and answer.get('blocked') is False
            row.update(generated_answer=generated, retrieved_contexts=contexts, source_ids=ids,
                       application_sources=answer.get('sources'), fallback=answer.get('fallback'),
                       faithfulness=float(valid_sources and supported and explicit_mock),
                       answer_relevancy=float(accepted and explicit_mock))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row['judge_error'] = type(exc).__name__
        rows.append(row)
    policy = load_policy(policy_path or Path(__file__).with_name('gate-policy.json'))
    return {'schema': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'execution_mode': 'synthetic_mock_http_fixture_acceptance', 'sample_size': len(rows),
            'dataset_id': dataset['dataset_id'], 'provenance': provenance,
            'metric_basis': 'binary fixture predicates; not LLM scores or semantic quality',
            'rows': rows, 'gate': assess_rows(rows, policy, len(dataset['cases']))}
