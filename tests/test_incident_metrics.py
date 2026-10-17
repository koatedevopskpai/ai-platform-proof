from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts/release'))
from incident_metrics import summarize


class IndicatorTests(unittest.TestCase):
    def test_alert_fires_and_clears_from_observations(self):
        healthy = [{'success': True, 'latency_ms': 10} for _ in range(12)]
        self.assertEqual(summarize(healthy)['alert_state'], 'clear')
        self.assertEqual(summarize([{'success': False, 'latency_ms': 10}])['alerts'], ['user_acceptance_error_rate'])

    def test_empty_or_nonfinite_observations_cannot_report_health(self):
        for rows in [[], [{'success': True, 'latency_ms': float('nan')}]]:
            with self.assertRaises(ValueError):
                summarize(rows)
