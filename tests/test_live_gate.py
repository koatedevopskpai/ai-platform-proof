"""Live-mode gate: structural predicates, policy validation and mode dispatch.

Live mode exists because a model answer is not a substring of its contexts, so mock mode's token
containment cannot judge it. The predicates here are structural and deterministic; model-judged
quality is deliberately excluded (see evidence/ragas-eval.md).
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts/release'))
import candidate_gate
import live_gate

LIVE_DATASET = Path(candidate_gate.__file__).with_name('acceptance-live.json')
MOCK_DATASET = Path(candidate_gate.__file__).with_name('acceptance.json')

CORPUS = {
    'o02-refund': 'Full refunds are available within 30 days of purchase.',
    'o02-software': 'Opened software is not refundable.',
}
CASE = {
    'id': 'refund',
    'question': 'What is the refund window?',
    'source_id': 'o02-refund',
    'required_phrases': ['30 days'],
    'forbidden_phrases': ['unlimited'],
}


def good_search(identifier='o02-refund'):
    return {'results': [{'id': identifier, 'content': CORPUS[identifier]}]}


def good_answer(identifier='o02-refund'):
    return {
        'answer': 'The refund window is 30 days from purchase.',
        'sources': [identifier],
        'fallback': False,
        'blocked': False,
        'generation_mode': 'provider',
        'usage': {'prompt_tokens': 200, 'completion_tokens': 12, 'total_tokens': 212},
    }


class PolicyTests(unittest.TestCase):
    def test_shipped_live_policy_loads(self):
        policy = live_gate.load_policy()
        self.assertEqual(policy['policy_id'], 'live-structural-v1')
        self.assertEqual(policy['min_pass_ratio'], 1.0)

    def _write(self, payload):
        handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(payload, handle)
        handle.close()
        return Path(handle.name)

    def test_unknown_check_is_rejected(self):
        path = self._write({'policy_id': 'x', 'required_checks': ['not_a_check'], 'min_pass_ratio': 1.0})
        with self.assertRaises(ValueError):
            live_gate.load_policy(path)

    def test_pass_ratio_must_be_within_range(self):
        for bad in (0, 1.5, 'all'):
            path = self._write({'policy_id': 'x', 'required_checks': ['provider_answer'],
                                'min_pass_ratio': bad})
            with self.assertRaises(ValueError):
                live_gate.load_policy(path)

    def test_missing_policy_id_is_rejected(self):
        path = self._write({'required_checks': ['provider_answer'], 'min_pass_ratio': 1.0})
        with self.assertRaises(ValueError):
            live_gate.load_policy(path)


class CaseCheckTests(unittest.TestCase):
    def test_a_good_live_response_passes_every_check(self):
        checks = live_gate.case_checks(CASE, CORPUS, good_search(), good_answer())
        self.assertTrue(all(checks.values()), checks)

    def test_deterministic_fallback_is_rejected(self):
        answer = good_answer()
        answer.update(generation_mode='deterministic_fallback', fallback=True,
                      usage=None)
        checks = live_gate.case_checks(CASE, CORPUS, good_search(), answer)
        self.assertFalse(checks['provider_answer'])
        self.assertFalse(checks['not_fallback'])
        self.assertFalse(checks['usage_present'])

    def test_missing_usage_is_rejected(self):
        answer = good_answer()
        answer['usage'] = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        self.assertFalse(live_gate.case_checks(CASE, CORPUS, good_search(), answer)['usage_present'])

    def test_citation_must_match_the_retrieved_set(self):
        answer = good_answer()
        answer['sources'] = ['o02-software']
        self.assertFalse(live_gate.case_checks(CASE, CORPUS, good_search(), answer)['sources_valid'])

    def test_expected_source_must_be_cited(self):
        case = dict(CASE, source_id='o02-software')
        self.assertFalse(live_gate.case_checks(case, CORPUS, good_search(), good_answer())['sources_valid'])

    def test_required_phrase_missing_is_rejected(self):
        answer = good_answer()
        answer['answer'] = 'Refunds are available for a while.'
        self.assertFalse(live_gate.case_checks(CASE, CORPUS, good_search(), answer)['required_phrases'])

    def test_forbidden_phrase_present_is_rejected(self):
        answer = good_answer()
        answer['answer'] = 'Refunds are unlimited within 30 days.'
        self.assertFalse(live_gate.case_checks(CASE, CORPUS, good_search(), answer)['forbidden_phrases'])

    def test_empty_answer_is_rejected(self):
        answer = good_answer()
        answer['answer'] = '   '
        self.assertFalse(live_gate.case_checks(CASE, CORPUS, good_search(), answer)['required_phrases'])


class AssessTests(unittest.TestCase):
    def setUp(self):
        self.policy = live_gate.load_policy()

    def row(self, case_id, **overrides):
        checks = live_gate.case_checks(CASE, CORPUS, good_search(), good_answer())
        checks.update(overrides)
        return {'case_id': case_id, 'checks': checks}

    def test_all_passing_cases_pass_the_gate(self):
        rows = [self.row('a'), self.row('b')]
        gate = live_gate.assess(rows, self.policy, 2)
        self.assertTrue(gate['passed'])
        self.assertEqual(gate['pass_ratio'], 1.0)

    def test_one_failing_case_fails_the_gate_and_names_the_check(self):
        rows = [self.row('a'), self.row('b', usage_present=False)]
        gate = live_gate.assess(rows, self.policy, 2)
        self.assertFalse(gate['passed'])
        failing = [f for f in gate['hard_failures'] if f.get('case') == 'b']
        self.assertEqual(failing[0]['checks'], ['usage_present'])

    def test_wrong_case_count_fails_closed(self):
        gate = live_gate.assess([self.row('a')], self.policy, 2)
        self.assertFalse(gate['passed'])
        self.assertIn('missing_or_unexpected_cases',
                      [f['reason'] for f in gate['hard_failures']])


class ModeDispatchTests(unittest.TestCase):
    def test_live_dataset_declares_live_mode(self):
        data = json.loads(LIVE_DATASET.read_text(encoding='utf-8'))
        self.assertEqual(data['execution_mode'], 'live_provider')
        candidate_gate.load_dataset(str(LIVE_DATASET))

    def test_mock_dataset_still_declares_mock_mode(self):
        data = json.loads(MOCK_DATASET.read_text(encoding='utf-8'))
        self.assertEqual(data['execution_mode'], 'synthetic_mock')
        candidate_gate.load_dataset(str(MOCK_DATASET))

    def test_unknown_mode_is_rejected(self):
        data = json.loads(MOCK_DATASET.read_text(encoding='utf-8'))
        data['execution_mode'] = 'something_else'
        handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(data, handle)
        handle.close()
        with self.assertRaises(ValueError):
            candidate_gate.load_dataset(handle.name)


if __name__ == '__main__':
    unittest.main()
