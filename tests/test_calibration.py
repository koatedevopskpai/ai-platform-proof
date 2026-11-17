"""Cohen's kappa arithmetic, checked against a hand-computed table.

The statistic is the one thing in the calibration tool that must be right before any labelling happens:
a wrong kappa would turn an hour of human work into a wrong number.

Written as a TestCase class because CI runs `python -m unittest discover`, which collects classes only —
bare `test_*` functions would be silently skipped.
"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "experiments"))

import judge_calibration as calibration


def pairs_from_table(yes_yes, yes_no, no_yes, no_no):
    return ([("yes", "yes")] * yes_yes + [("yes", "no")] * yes_no
            + [("no", "yes")] * no_yes + [("no", "no")] * no_no)


class KappaTests(unittest.TestCase):
    def test_known_two_by_two_table(self):
        # po = 35/50 = 0.70, pe = 0.5*0.6 + 0.5*0.4 = 0.50, kappa = (0.70-0.50)/0.50 = 0.40
        stats = calibration.cohens_kappa(pairs_from_table(20, 5, 10, 15))
        self.assertEqual(stats["n"], 50)
        self.assertEqual(stats["observed_agreement"], 0.7)
        self.assertEqual(stats["expected_agreement"], 0.5)
        self.assertEqual(stats["kappa"], 0.4)
        self.assertEqual(stats["band"], "fair")

    def test_perfect_agreement_is_one(self):
        stats = calibration.cohens_kappa(pairs_from_table(10, 0, 0, 10))
        self.assertEqual(stats["kappa"], 1.0)
        self.assertEqual(stats["band"], "almost perfect")

    def test_total_disagreement_is_below_chance(self):
        stats = calibration.cohens_kappa(pairs_from_table(0, 10, 10, 0))
        self.assertEqual(stats["kappa"], -1.0)
        self.assertEqual(stats["band"], "worse than chance")

    def test_confusion_counts_are_recorded(self):
        stats = calibration.cohens_kappa(pairs_from_table(20, 5, 10, 15))
        self.assertEqual(stats["confusion"], {
            "human_yes_judge_yes": 20,
            "human_yes_judge_no": 5,
            "human_no_judge_yes": 10,
            "human_no_judge_no": 15,
        })

    def test_empty_input_returns_no_statistics(self):
        stats = calibration.cohens_kappa([])
        self.assertEqual(stats["n"], 0)
        self.assertIsNone(stats["kappa"])

    def test_interval_brackets_the_estimate(self):
        stats = calibration.cohens_kappa(pairs_from_table(20, 5, 10, 15))
        low, high = stats["ci95"]
        self.assertLess(low, stats["kappa"])
        self.assertGreater(high, stats["kappa"])

    def test_band_boundaries(self):
        self.assertEqual(calibration.band(0.2), "slight")
        self.assertEqual(calibration.band(0.41), "moderate")
        self.assertEqual(calibration.band(0.61), "substantial")
        self.assertEqual(calibration.band(0.9), "almost perfect")


class ItemSetTests(unittest.TestCase):
    def test_items_are_spread_across_plausible_failure_modes(self):
        items = calibration.build_items(per_document=10, seed=1)
        variants = {item["variant"] for item in items}
        self.assertIn("verbatim", variants)
        self.assertIn("negated", variants)
        self.assertIn("cross_document", variants)
        self.assertGreater(len(items), 20)

    def test_item_ids_are_unique(self):
        items = calibration.build_items(per_document=10, seed=1)
        ids = [item["item_id"] for item in items]
        self.assertEqual(len(ids), len(set(ids)))

    def test_generation_is_deterministic_for_a_seed(self):
        first = calibration.build_items(per_document=10, seed=7)
        second = calibration.build_items(per_document=10, seed=7)
        self.assertEqual([i["answer"] for i in first], [i["answer"] for i in second])


if __name__ == "__main__":
    unittest.main()
