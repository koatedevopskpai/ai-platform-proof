"""Judge calibration: blind labelling, Cohen's kappa, and agreement statistics.

Cohen's kappa measures agreement between the judge and a **human**. It cannot be automated: labels
produced by a model give model-versus-model agreement, and reporting that as kappa would be a
misrepresentation. So this tool has three phases, and the middle one is yours.

    --generate   build an item set from the fixture documents, spread across plausible failure modes
    --label      show context and answer, collect a verdict, reveal nothing about the judge
    --score      run the judge on the same items and compute agreement

Blinding is enforced by construction: `--label` never calls the judge, and `--score` refuses to run
until every item has a label.

The labelling task is binary — *is every factual claim in the answer supported by the context?* — because
a binary decision has a stable kappa. Three-point scales need weighted kappa and produce wider intervals
for the same effort.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("OUT_DIR", REPO / "artifacts" / "experiments"))
DATASET = REPO / "services" / "evaluator" / "datasets" / "ragas_set.jsonl"

FILLER = (" This is provided for general guidance only and may change without notice; it does not "
          "constitute advice.")
NEGATION = re.compile(r"\b(is|are|must|can|may|requires?)\b")


def post(base_url: str, api_key: str, payload: dict, timeout: int = 60) -> dict:
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def truncate(text: str, fraction: float) -> str:
    words = text.split()
    keep = max(2, int(len(words) * fraction))
    return " ".join(words[:keep]).rstrip(",;:")


def negation(text: str) -> str:
    """Invert the first modal verb. Crude, but it produces a claim the context does not support."""
    match = NEGATION.search(text)
    if not match:
        return "It is not the case that " + text[0].lower() + text[1:]
    return text[:match.start()] + "is not" + text[match.end():]


def build_items(per_document: int, seed: int) -> list[dict]:
    """Ten variants per document, spread across supported and unsupported answers."""
    rng = random.Random(seed)
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for row in rows:
        doc_id, context, reference = row["id"], row["document"], row["reference"]
        other = rows[(rows.index(row) + 1) % len(rows)]

        candidates = [
            ("verbatim", reference, "supported"),
            ("padded", reference + FILLER, "supported"),
            ("truncated_85", truncate(reference, 0.85), "partial"),
            ("truncated_70", truncate(reference, 0.7), "partial"),
            ("truncated_55", truncate(reference, 0.55), "partial"),
            ("truncated_40", truncate(reference, 0.4), "partial"),
            ("negated", negation(reference), "unsupported"),
            ("negated_padded", negation(reference) + FILLER, "unsupported"),
            ("cross_document", other["reference"], "unsupported"),
            ("other_context", other["document"], "unsupported"),
            ("prefix_only", reference.split(",")[0], "partial"),
            ("hedged", reference + " This is subject to approval.", "unsupported"),
            ("verbatim_shuffled", " ".join(rng.sample(reference.split(), k=len(reference.split()))),
             "unsupported"),
            ("other_context_padded", other["document"] + FILLER, "unsupported"),
        ]

        for kind, answer, _expected in candidates[:per_document]:
            key = (doc_id, answer)
            if key in seen or not answer.strip():
                continue
            seen.add(key)
            items.append({
                "item_id": f"{doc_id}-{kind}",
                "document_id": doc_id,
                "context": context,
                "answer": answer,
                "variant": kind,
                # Deliberately not a label: the human decides. Kept so the receipt can show which
                # variants the labeller and the judge actually agreed on.
            })

    rng.shuffle(items)
    return items


def judge_verdict(model: str, base_url: str, api_key: str, context: str, answer: str) -> str | None:
    body = post(base_url, api_key, {
        "model": model,
        "messages": [{"role": "user", "content":
                      "CONTEXT:\n" + context + "\n\nANSWER:\n" + answer +
                      "\n\nIs every factual claim in the ANSWER supported by the CONTEXT? "
                      "Reply YES or NO only."}],
        "temperature": 0.0,
        "max_tokens": 4,
    })
    text = (body["choices"][0]["message"]["content"] or "").strip().upper()
    if text.startswith("YES"):
        return "yes"
    if text.startswith("NO"):
        return "no"
    return None


def cohens_kappa(pairs: list[tuple[str, str]]) -> dict:
    n = len(pairs)
    if n == 0:
        return {"n": 0, "kappa": None, "observed_agreement": None}

    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n
    human_yes = sum(1 for a, _ in pairs if a == "yes") / n
    judge_yes = sum(1 for _, b in pairs if b == "yes") / n
    pe = human_yes * judge_yes + (1 - human_yes) * (1 - judge_yes)

    kappa = None if pe >= 1 else (po - pe) / (1 - pe)
    interval = None
    if kappa is not None and po < 1:
        se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
        interval = [round(kappa - 1.96 * se, 4), round(kappa + 1.96 * se, 4)]

    return {
        "n": n,
        "observed_agreement": round(po, 4),
        "expected_agreement": round(pe, 4),
        "kappa": None if kappa is None else round(kappa, 4),
        "ci95": interval,
        "band": band(kappa),
        "confusion": {
            "human_yes_judge_yes": sum(1 for a, b in pairs if a == "yes" and b == "yes"),
            "human_yes_judge_no": sum(1 for a, b in pairs if a == "yes" and b == "no"),
            "human_no_judge_yes": sum(1 for a, b in pairs if a == "no" and b == "yes"),
            "human_no_judge_no": sum(1 for a, b in pairs if a == "no" and b == "no"),
        },
    }


def band(kappa: float | None) -> str | None:
    if kappa is None:
        return None
    if kappa < 0:
        return "worse than chance"
    if kappa <= 0.20:
        return "slight"
    if kappa <= 0.40:
        return "fair"
    if kappa <= 0.60:
        return "moderate"
    if kappa <= 0.80:
        return "substantial"
    return "almost perfect"


def cmd_generate(args) -> int:
    items = build_items(args.per_document, args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "calibration-items.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in items) + "\n", encoding="utf-8")
    print(json.dumps({"items": len(items),
                      "variants": sorted({item["variant"] for item in items}),
                      "path": str(path)}, indent=2))
    return 0


def cmd_label(args) -> int:
    items = [json.loads(line) for line in Path(args.items).read_text(encoding="utf-8").splitlines() if line.strip()]
    labels_path = Path(args.labels)
    labels = {}
    if labels_path.exists():
        for line in labels_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                labels[row["item_id"]] = row["label"]

    pending = [item for item in items if item["item_id"] not in labels]
    if not pending:
        print(f"all {len(items)} items labelled")
        return 0

    print(f"{len(pending)} of {len(items)} items remaining.")
    print("Is every factual claim in the ANSWER supported by the CONTEXT?")
    print("  y = yes, n = no, s = skip, q = save and quit\n")

    for position, item in enumerate(pending, 1):
        print(f"--- item {position}/{len(pending)}  ({item['item_id']}) ---")
        print(f"CONTEXT: {item['context']}\n")
        print(f"ANSWER:  {item['answer']}\n")
        while True:
            reply = input("supported? [y/n/s/q] ").strip().lower()
            if reply in ("y", "n"):
                labels[item["item_id"]] = "yes" if reply == "y" else "no"
                break
            if reply == "s":
                break
            if reply == "q":
                labels_path.parent.mkdir(parents=True, exist_ok=True)
                labels_path.write_text(
                    "\n".join(json.dumps({"item_id": k, "label": v}) for k, v in labels.items()) + "\n",
                    encoding="utf-8")
                print(f"saved {len(labels)} labels")
                return 0
            print("  enter y, n, s or q")

    labels_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path.write_text(
        "\n".join(json.dumps({"item_id": k, "label": v}) for k, v in labels.items()) + "\n",
        encoding="utf-8")
    print(f"saved {len(labels)} labels to {labels_path}")
    return 0


def cmd_score(args) -> int:
    items = {json.loads(line)["item_id"]: json.loads(line)
             for line in Path(args.items).read_text(encoding="utf-8").splitlines() if line.strip()}
    labels = {}
    for line in Path(args.labels).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            labels[row["item_id"]] = row["label"]

    missing = [item_id for item_id in items if item_id not in labels]
    if missing:
        print(f"refusing to score: {len(missing)} of {len(items)} items are unlabelled")
        return 2

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("refusing to run: OPENAI_API_KEY is not set")
        return 2

    pairs, rows = [], []
    errors = 0
    for item_id, item in items.items():
        verdict = judge_verdict(args.judge, args.judge_base_url, api_key, item["context"], item["answer"])
        if verdict is None:
            errors += 1
            continue
        pairs.append((labels[item_id], verdict))
        rows.append({"item_id": item_id, "variant": item["variant"],
                     "human": labels[item_id], "judge": verdict})

    stats = cohens_kappa(pairs)
    receipt = {
        "run_id": "calibration-" + args.label,
        "grade": "E4",
        "judge": args.judge,
        "items": len(items),
        "judge_errors": errors,
        "dataset": str(Path(args.items).name),
        "labeller": "single annotator; judge-versus-author agreement, not inter-annotator agreement",
        "statistics": stats,
        "per_variant": variant_agreement(rows),
        "rows": rows,
        "limits": [
            "single annotator, so this is not inter-annotator agreement",
            "items are synthetic perturbations of six fixture documents",
            "a binary task; three-point scales would need weighted kappa",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"calibration-{args.label}.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"label": args.label, "statistics": stats,
                      "judge_errors": errors, "receipt": str(path)}, indent=2))
    return 0


def variant_agreement(rows: list[dict]) -> dict:
    buckets: dict[str, list[int]] = {}
    for row in rows:
        buckets.setdefault(row["variant"], []).append(1 if row["human"] == row["judge"] else 0)
    return {variant: {"n": len(values), "agreement": round(sum(values) / len(values), 4)}
            for variant, values in sorted(buckets.items())}


def main() -> int:
    parser = argparse.ArgumentParser(description="Blind judge calibration.")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="build the item set")
    generate.add_argument("--per-document", type=int, default=10)
    generate.add_argument("--seed", type=int, default=20261117)
    generate.set_defaults(handler=cmd_generate)

    label = sub.add_parser("label", help="label items blind")
    label.add_argument("--items", default=str(OUT / "calibration-items.jsonl"))
    label.add_argument("--labels", default=str(OUT / "calibration-labels.jsonl"))
    label.set_defaults(handler=cmd_label)

    score = sub.add_parser("score", help="run the judge and compute agreement")
    score.add_argument("--items", default=str(OUT / "calibration-items.jsonl"))
    score.add_argument("--labels", default=str(OUT / "calibration-labels.jsonl"))
    score.add_argument("--judge", default=os.environ.get("JUDGE_MODEL", "gpt-4o-mini"))
    score.add_argument("--judge-base-url", default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    score.add_argument("--label", default="kappa")
    score.set_defaults(handler=cmd_score)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
