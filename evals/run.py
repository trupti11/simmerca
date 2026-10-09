"""Eval harness. Exit code 1 if any score is below evals/thresholds.json (CI gate).

Usage:
  PYTHONPATH=src python evals/run.py              # offline: rules parser + recorded model outputs
  PYTHONPATH=src python evals/run.py --live       # also calls Bedrock (needs AWS creds + model env vars)
  PYTHONPATH=src python evals/run.py --show-misses
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

from simmerca.intelligence.extractor import normalize_output  # noqa: E402
from simmerca.whatsapp.parser import parse  # noqa: E402


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / "datasets" / name).read_text(encoding="utf-8").splitlines() if line]


def eval_whatsapp(llm=None, show_misses=False) -> dict:
    rows = load("whatsapp_messages.jsonl")
    hits = Counter()
    totals = Counter()
    by_lang: dict[str, Counter] = defaultdict(Counter)
    misses = []
    for r in rows:
        p = parse(r["text"], llm=llm)
        ok_intent = p.intent == r["intent"]
        totals["intent"] += 1
        hits["intent"] += ok_intent
        by_lang[r["lang"]]["n"] += 1
        by_lang[r["lang"]]["ok"] += ok_intent
        if r["sku"] is not None:
            totals["sku"] += 1
            hits["sku"] += p.sku == r["sku"]
        if r["qty"] is not None:
            totals["qty"] += 1
            hits["qty"] += p.qty == r["qty"]
        if not ok_intent or (r["sku"] and p.sku != r["sku"]) or (r["qty"] is not None and p.qty != r["qty"]):
            misses.append((r["text"], r["intent"], r["sku"], r["qty"], p.intent, p.sku, p.qty))
    if show_misses:
        for m in misses:
            print("  MISS", m)
    per_lang = {k: round(v["ok"] / v["n"], 3) for k, v in sorted(by_lang.items())}
    return {
        "n": len(rows),
        "intent_accuracy": round(hits["intent"] / totals["intent"], 4),
        # one language collapsing must fail the gate even if the overall average looks fine
        "min_language_intent_accuracy": min(per_lang.values()),
        "sku_accuracy": round(hits["sku"] / max(1, totals["sku"]), 4),
        "qty_accuracy": round(hits["qty"] / max(1, totals["qty"]), 4),
        "intent_by_language": per_lang,
    }


def eval_attributes(model=None, show_misses=False) -> dict:
    rows = load("attributes_seed.jsonl")
    hits = Counter()
    for r in rows:
        if model is not None and r.get("image_key"):
            raw = model.describe([(Path(r["image_key"]).read_bytes(), "image/jpeg")], _prompt())
        else:
            raw = r["recorded_output"]
        out = normalize_output(raw)
        for field in ("fabric", "primary_color"):
            ok = out[field]["value"] == r["label"][field]
            hits[field] += ok
            if show_misses and not ok:
                print("  MISS", r["id"], field, "expected", r["label"][field], "got", out[field]["value"])
    n = len(rows)
    return {
        "n": n,
        "source": "live" if model is not None else "recorded (seed set - replace with real labeled photos)",
        "fabric_accuracy": round(hits["fabric"] / n, 4),
        "primary_color_accuracy": round(hits["primary_color"] / n, 4),
    }


def _prompt() -> str:
    from simmerca.intelligence.extractor import PROMPT

    return PROMPT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--show-misses", action="store_true")
    args = ap.parse_args()

    llm = vision = None
    if args.live:
        from simmerca.aws import BedrockText, BedrockVision

        if os.environ.get("BEDROCK_TEXT_MODEL_ID"):
            llm = BedrockText(os.environ["BEDROCK_TEXT_MODEL_ID"])
        if os.environ.get("BEDROCK_VISION_MODEL_ID"):
            vision = BedrockVision(os.environ["BEDROCK_VISION_MODEL_ID"])

    thresholds = json.loads((ROOT / "thresholds.json").read_text())
    results = {"whatsapp": eval_whatsapp(llm, args.show_misses), "attributes": eval_attributes(vision, args.show_misses)}
    print(json.dumps(results, indent=2, ensure_ascii=False))

    failed = [
        f"{suite}.{metric}: {results[suite][metric]} < {minimum}"
        for suite, metrics in thresholds.items()
        for metric, minimum in metrics.items()
        if results[suite][metric] < minimum
    ]
    (ROOT / "last_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    if failed:
        print("\nEVAL GATE FAILED:\n  " + "\n  ".join(failed))
        return 1
    print("\nEVAL GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
