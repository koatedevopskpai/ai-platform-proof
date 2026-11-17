"""Unit-economics arithmetic and the ledger join.

Each metric has a denominator that can be zero, missing, or unattributable, so the cases worth testing are
the degenerate ones: a run that accepted nothing, a run with spend but no outcome, and a ledger holding
entries that cannot be priced. Getting those wrong produces a plausible number that is wrong.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "experiments"))

import unit_economics as economics


def usage(label, model, tokens, cost):
    return {"event": "token.usage", "label": label, "model": model,
            "total_tokens": tokens, "cost_usd": cost}


def outcome(label, attempted, accepted, passed=True):
    return {"event": "run.outcome", "label": label, "policy": "live-structural-v1",
            "attempted": attempted, "accepted": accepted, "passed": passed}


class MetricTests(unittest.TestCase):
    def test_cost_per_accepted_answer(self):
        self.assertEqual(economics.cost_per_accepted(0.001, 4), 0.00025)

    def test_cost_per_accepted_is_undefined_when_nothing_was_accepted(self):
        # An infinite cost is not a number a report can print.
        self.assertIsNone(economics.cost_per_accepted(0.001, 0))
        self.assertIsNone(economics.cost_per_accepted(0.001, None))

    def test_cost_per_1k_tokens(self):
        self.assertEqual(economics.cost_per_1k_tokens(0.002, 1000), 0.002)
        self.assertEqual(economics.cost_per_1k_tokens(0.001, 500), 0.002)

    def test_cost_per_1k_tokens_is_undefined_without_tokens(self):
        self.assertIsNone(economics.cost_per_1k_tokens(0.001, 0))
        self.assertIsNone(economics.cost_per_1k_tokens(None, 100))


class ShareTests(unittest.TestCase):
    def test_shares_sum_to_one(self):
        rows = economics.share_by([usage("r", "a", 100, 0.0003), usage("r", "b", 100, 0.0001)], "model")
        self.assertAlmostEqual(sum(r["share"] for r in rows), 1.0, places=4)

    def test_unpriced_calls_are_counted_not_counted_as_zero_value(self):
        rows = economics.share_by([usage("r", "a", 100, 0.0003),
                                   usage("r", "unknown", 100, None)], "model")
        priced = next(r for r in rows if r["key"] == "a")
        self.assertEqual(priced["share"], 1.0)
        self.assertTrue(any(r["key"] == "unknown" for r in rows))

    def test_missing_attribution_falls_into_a_named_bucket(self):
        rows = economics.share_by([usage(None, "a", 10, 0.0001)], "label")
        self.assertEqual(rows[0]["key"], "(none)")


class JoinTests(unittest.TestCase):
    def test_usage_and_outcome_join_on_label(self):
        runs = economics.join_by_label([usage("gate", "m", 300, 0.001)], [outcome("gate", 3, 3)])
        self.assertEqual(runs["gate"]["accepted"], 3)
        self.assertEqual(runs["gate"]["cost_per_accepted_usd"], 0.000333333)

    def test_spend_without_an_outcome_is_kept_and_marked_unjoined(self):
        runs = economics.join_by_label([usage("receipt", "m", 300, 0.001)], [])
        self.assertIsNone(runs["receipt"]["attempted"])
        self.assertIsNone(runs["receipt"]["cost_per_accepted_usd"])

    def test_an_outcome_without_spend_is_kept(self):
        runs = economics.join_by_label([], [outcome("empty", 3, 0)])
        self.assertEqual(runs["empty"]["calls"], 0)
        self.assertIsNone(runs["empty"]["cost_per_accepted_usd"])


class ReportTests(unittest.TestCase):
    def write_ledger(self, tmp, entries):
        path = Path(tmp) / "ledger.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        return path

    def test_load_ledger_ignores_other_event_types(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ledger(tmp, [usage("a", "m", 1, 0.1),
                                           {"event": "span"}, outcome("a", 1, 1)])
            usage_rows, outcome_rows = economics.load_ledger(path)
        self.assertEqual(len(usage_rows), 1)
        self.assertEqual(len(outcome_rows), 1)

    def test_report_separates_joined_from_unjoined(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ledger(tmp, [usage("gate", "m", 300, 0.001), outcome("gate", 3, 3),
                                           usage("receipt", "m", 100, 0.0002)])
            report = economics.build_report(path)
        self.assertEqual(len(report["runs_with_outcome"]), 1)
        self.assertEqual(len(report["spend_without_outcome"]), 1)
        self.assertEqual(report["total_cost_usd"], 0.0012)

    def test_markdown_names_the_run_and_the_unjoined_spend(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_ledger(tmp, [usage("gate", "m", 300, 0.001), outcome("gate", 3, 3),
                                           usage("receipt", "m", 100, 0.0002)])
            markdown = economics.render_markdown(economics.build_report(path))
        self.assertIn("`gate`", markdown)
        self.assertIn("3/3", markdown)
        self.assertIn("Spend with no recorded outcome", markdown)
        self.assertIn("`receipt`", markdown)


if __name__ == "__main__":
    unittest.main()
