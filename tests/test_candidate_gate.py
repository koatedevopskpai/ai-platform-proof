import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts/release'))
import candidate_gate
import gate_policy

DATASET = Path(candidate_gate.__file__).with_name('acceptance.json')


class CandidateTests(unittest.TestCase):
    def api(self, bad=False, missing_source=False):
        data = candidate_gate.load_dataset(DATASET)
        documents = data['documents']
        def request(base, route, payload):
            if route == '/ingest':
                return {'id': payload['id'], 'dimensions': 384}
            if route == '/search':
                return {'results': [] if missing_source else documents}
            return {'answer': 'Refunds are never permitted.' if bad else
                    'Per the source material: ' + ' '.join(d['content'] for d in documents),
                    'sources': [d['id'] for d in documents], 'fallback': True, 'blocked': False}
        return request

    def test_acceptance_and_wrong_generated_answer_with_correct_sources(self):
        correct = candidate_gate.evaluate('http://fixture', DATASET, {}, request=self.api())
        wrong = candidate_gate.evaluate('http://fixture', DATASET, {}, request=self.api(bad=True))
        self.assertTrue(correct['gate']['passed'])
        self.assertFalse(wrong['gate']['passed'])
        self.assertEqual(wrong['rows'][0]['generated_answer'], 'Refunds are never permitted.')

    def test_missing_context_blocks_even_if_answer_is_present(self):
        report = candidate_gate.evaluate('http://fixture', DATASET, {}, request=self.api(missing_source=True))
        self.assertFalse(report['gate']['passed'])
        self.assertTrue(any(f['reason'] == 'missing_context' for f in report['gate']['hard_failures']))

    def test_missing_empty_and_duplicate_dataset_block(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'data.json'
            with self.assertRaises(FileNotFoundError):
                candidate_gate.load_dataset(path)
            data = json.loads(DATASET.read_text())
            data['cases'] = []
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                candidate_gate.load_dataset(path)
            data = json.loads(DATASET.read_text()); data['cases'].append(data['cases'][0])
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                candidate_gate.load_dataset(path)

    def test_http_error_becomes_failed_case(self):
        original = self.api()
        def fail(base, route, payload):
            if route == '/rag': raise TimeoutError('fixture')
            return original(base, route, payload)
        report = candidate_gate.evaluate('http://fixture', DATASET, {}, request=fail)
        self.assertFalse(report['gate']['passed'])
        self.assertEqual(len(report['rows']), 3)

    def test_e04_rejects_missing_nan_invented_citation_and_judge_error(self):
        row = {'question': 'fixture', 'generated_answer': 'fixture', 'retrieved_contexts': ['fixture'],
               'faithfulness': 1, 'answer_relevancy': 1}
        policy = gate_policy.load_policy(Path(candidate_gate.__file__).with_name('gate-policy.json'))
        for patch in [{'faithfulness': None}, {'faithfulness': float('nan')},
                      {'generated_answer': 'fixture [99]'}, {'judge_error': 'fixture'}]:
            with self.subTest(patch=patch):
                self.assertFalse(gate_policy.assess_rows([dict(row, **patch)], policy, 1)['passed'])


if __name__ == '__main__':
    unittest.main()
