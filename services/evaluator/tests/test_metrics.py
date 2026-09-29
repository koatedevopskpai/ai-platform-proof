import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluator import metrics


def test_rouge_l_perfect_match_is_1():
    assert metrics.rouge_l("refunds within 30 days", "refunds within 30 days") == 1.0


def test_rouge_l_disjoint_is_0():
    assert metrics.rouge_l("hello world", "refund policy") == 0.0


def test_map_at_k():
    # rank1 hit (p@1=1.0) + rank3 hit (p@3=2/3) -> AP = (1.0 + 0.6667)/2
    assert metrics.map_at_k({"a", "b"}, ["a", "c", "b"]) == 0.8333333333333333
    assert metrics.map_at_k({"a"}, ["b", "a"]) == 0.5


def test_ndcg_at_k():
    assert metrics.ndcg_at_k({"a", "b"}, ["a", "b"]) == 1.0
    assert metrics.ndcg_at_k({"a"}, ["a"]) == 1.0


def test_faithfulness_grounded_in_context():
    ctx = ["refunds are allowed within 30 days"]
    assert metrics.faithfulness("refunds within 30 days", ctx) > 0.9


def test_answer_relevancy_overlap():
    assert metrics.answer_relevancy("what is the refund window", "refund window is 30 days") > 0.0