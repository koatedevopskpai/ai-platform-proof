"""Guards against the ragas version-churn hazard encountered while building this.

On ragas 0.4.4 the modern `ragas.metrics.collections` classes are a different base type from
`ragas.metrics.Metric`, and `evaluate()` rejects them. The evaluation path therefore uses the legacy
singletons. These tests fail if a version bump moves the metrics, renames them, or changes the
compatibility rules — rather than silently dropping a column from a receipt.
"""
from ragas.metrics import Metric

from evaluator.ragas_eval import METRIC_NAMES, build_metrics


def test_build_metrics_returns_the_declared_metric_names():
    metrics = build_metrics()
    assert [m.name for m in metrics] == list(METRIC_NAMES)


def test_every_metric_is_acceptable_to_evaluate():
    # evaluate() requires ragas.metrics.Metric instances. The collections classes are BaseMetric
    # and are rejected, so this asserts the working path rather than the documented future one.
    for metric in build_metrics():
        assert isinstance(metric, Metric), f"{metric!r} is not a ragas Metric"


def test_answer_relevancy_declares_single_generation_strictness():
    # The LangChain wrapper returns one generation, so strictness > 1 would silently degrade to a
    # single sample while implying an average. The metric states what actually happens.
    metrics = {m.name: m for m in build_metrics()}
    assert metrics["answer_relevancy"].strictness == 1
