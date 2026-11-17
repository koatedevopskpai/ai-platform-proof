"""Judge bias probes: verbosity, position and self-preference.

These need **no human labels**. Each is a controlled perturbation where the content is held constant and
an irrelevant property changes; if the judge's verdict moves, that is bias.

What this does not do is measure whether the judge is *right* — that is Cohen's kappa, which needs blind
human labels (`scripts/experiments/judge_calibration.py`).

Probes:

* **verbosity** — score the answer, then score it with neutral filler appended. Content is unchanged;
  only length moved.
* **position** — judge a pair in both orders. A content-driven judge picks the same answer twice; one
  that prefers whichever came first does not.
* **self-preference** — score answers from two models to the same questions with model identity hidden,
  then compare means. Confounded by genuine quality differences, so it is reported as a gap, not a
  verdict.

Report **rates**, never verdicts. "Verbosity shifted the mean by 0.08" is a measurement; "the judge is
biased" is an accusation the data cannot support at these sample sizes.

Requires answer receipts produced by `live_receipt.py`, plus the fixture documents they refer to.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))
RATES = REPO / "services" / "rag-api" / "app" / "core" / "token_rates.json"

FILLER = (
    " This information is provided for general guidance and should be read alongside the full "
    "policy. It does not constitute advice, and it may be updated without notice. Readers should "
    "confirm the current position before relying on it."
)

NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def post(base_url: str, api_key: str, payload: dict, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


class Judge:
    def __init__(self, model: str, base_url: str, api_key: str) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _ask(self, prompt: str, max_tokens: int = 8) -> str:
        body = post(self.base_url, self.api_key, {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        })
        self.calls += 1
        usage = body.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        return (body["choices"][0]["message"]["content"] or "").strip()

    def score(self, context: str, answer: str) -> float | None:
        text = self._ask(
            "CONTEXT:\n" + context + "\n\nANSWER:\n" + answer +
            "\n\nRate from 0.0 to 1.0 how fully the ANSWER is supported by the CONTEXT. "
            "Reply with one number only."
        )
        match = NUMBER.search(text)
        if not match:
            return None
        value = float(match.group())
        return max(0.0, min(1.0, value))

    def prefer(self, context: str, first: str, second: str) -> str | None:
        text = self._ask(
            "CONTEXT:\n" + context + "\n\nA:\n" + first + "\n\nB:\n" + second +
            "\n\nWhich answer is better supported by the CONTEXT? Reply A or B only.",
            max_tokens=4,
        ).upper()
        for letter in ("A", "B"):
            if letter in text:
                return letter
        return None


def load_documents(path: Path) -> dict:
    """Fixture documents, from an acceptance dataset or a JSONL evaluation set."""
    if path.suffix == ".jsonl":
        docs = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                docs[row["id"]] = row["document"]
        return docs
    data = json.loads(path.read_text(encoding="utf-8"))
    return {doc["id"]: doc["content"] for doc in data.get("documents", [])}


def load_answers(path: Path, documents: dict) -> list[dict]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for case in receipt.get("cases", []):
        contexts = [documents[i] for i in case.get("retrieved_ids", []) if i in documents]
        if not contexts or not case.get("answer"):
            continue
        rows.append({
            "case_id": case["case_id"],
            "context": "\n".join(contexts),
            "answer": case["answer"],
            "source": receipt.get("label") or receipt.get("generation", {}).get("model", "unknown"),
        })
    return rows


def truncate(answer: str) -> str:
    """A worse answer with no added content: keep the opening, drop the rest."""
    words = answer.split()
    keep = max(2, len(words) // 3)
    shortened = " ".join(words[:keep]).rstrip(",;:")
    return shortened if shortened != answer else answer + " (truncated)"


def price(prompt_tokens: int, completion_tokens: int, model: str) -> float | None:
    if not RATES.exists():
        return None
    table = json.loads(RATES.read_text(encoding="utf-8"))
    row = table["models"].get(model) or table["models"].get(re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model))
    if not row:
        return None
    return round((prompt_tokens / 1_000_000) * row["input_per_million"]
                 + (completion_tokens / 1_000_000) * row["output_per_million"], 9)


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Judge bias probes.")
    parser.add_argument("--answers", required=True, action="append",
                        help="answer receipt JSON; repeat to compare models")
    parser.add_argument("--documents", required=True,
                        help="acceptance.json, or a JSONL set with id and document fields")
    parser.add_argument("--judge", default=os.environ.get("JUDGE_MODEL", "gpt-4o-mini"))
    parser.add_argument("--judge-base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--label", default="bias")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("refusing to run: OPENAI_API_KEY is not set")
        return 2

    documents = load_documents(Path(args.documents))
    sets = [load_answers(Path(p), documents) for p in args.answers]
    primary = sets[0]
    if not primary:
        print("no usable answers in the first receipt")
        return 2

    judge = Judge(args.judge, args.judge_base_url, api_key)

    # Verbosity: identical content, more words.
    verbosity = []
    for row in primary:
        original = judge.score(row["context"], row["answer"])
        padded = judge.score(row["context"], row["answer"] + FILLER)
        if original is None or padded is None:
            continue
        verbosity.append({"case_id": row["case_id"], "original": original, "padded": padded,
                          "delta": round(padded - original, 4)})

    # Position: same pair, both orders.
    position = []
    for row in primary:
        good, bad = row["answer"], truncate(row["answer"])
        if bad == good:
            continue
        first_pass = judge.prefer(row["context"], good, bad)
        second_pass = judge.prefer(row["context"], bad, good)
        if first_pass is None or second_pass is None:
            continue
        chosen_first = (first_pass == "A")   # good when listed first, bad when listed second
        chosen_second = (second_pass == "B")  # good when listed second
        position.append({
            "case_id": row["case_id"],
            "chose_good_when_first": chosen_first,
            "chose_good_when_second": chosen_second,
            "content_flipped": chosen_first != chosen_second,
        })

    # Self-preference: same questions, different model, identity hidden.
    preference = None
    if len(sets) > 1:
        by_source: dict[str, list[float]] = {}
        for rows in sets:
            for row in rows:
                value = judge.score(row["context"], row["answer"])
                if value is not None:
                    by_source.setdefault(row["source"], []).append(value)
        means = {source: mean(values) for source, values in by_source.items()}
        ordered = sorted(means.items(), key=lambda item: (item[1] is None, -(item[1] or 0)))
        preference = {
            "means_by_source": means,
            "gap": round(ordered[0][1] - ordered[-1][1], 4) if len(ordered) > 1 else None,
            "caveat": "genuine quality differences are confounded with self-preference",
        }

    receipt = {
        "run_id": "bias-" + args.label,
        "grade": "E3",
        "judge": args.judge,
        "label": args.label,
        "samples": len(primary),
        "verbosity": {
            "mean_original": mean([r["original"] for r in verbosity]),
            "mean_padded": mean([r["padded"] for r in verbosity]),
            "mean_delta": mean([r["delta"] for r in verbosity]),
            "items": verbosity,
        },
        "position": {
            "pairs": len(position),
            "content_flip_rate": round(sum(1 for r in position if r["content_flipped"]) / len(position), 4)
            if position else None,
            "always_first_chosen": all(r["chose_good_when_first"] and not r["chose_good_when_second"]
                                       for r in position) if position else None,
            "items": position,
        },
        "self_preference": preference,
        "calls": judge.calls,
        "tokens": {"prompt": judge.prompt_tokens, "completion": judge.completion_tokens},
        "estimated_cost_usd": price(judge.prompt_tokens, judge.completion_tokens, args.judge),
        "limits": [
            "small sample; report rates, not verdicts",
            "self-preference is confounded with genuine quality differences",
            "truncation is one degradation; real answers fail in more varied ways",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"judge-bias-{args.label}.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    print(json.dumps({
        "label": args.label,
        "samples": receipt["samples"],
        "verbosity_mean_delta": receipt["verbosity"]["mean_delta"],
        "position_content_flip_rate": receipt["position"]["content_flip_rate"],
        "self_preference_gap": (preference or {}).get("gap"),
        "calls": judge.calls,
        "estimated_cost_usd": receipt["estimated_cost_usd"],
        "receipt": str(path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
