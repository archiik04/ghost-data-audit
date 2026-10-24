"""Render code/evaluation/usage_report.md from the run report of the final full-dataset run.

    python code/evaluation/write_usage_report.py
Pricing is USD per 1M tokens (standard tier, no batch/caching discounts), as published
on the OpenAI model pages at the time of the run; adjust PRICES if they change.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRICES = {  # model: (input, output) USD per 1M tokens
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.4-nano": (0.20, 1.25),
}
PURPOSE = {"gpt-5.4-mini": "request-intent classification (3-vote majority)",
           "gpt-5.4": "screenshot transcription (vision)"}


def main():
    rep = json.loads((HERE / "out" / "run_report.json").read_text(encoding="utf-8"))
    usage = rep["model_usage"]
    n = rep["requests"]
    rows, tot_in, tot_out, tot_cost, tot_calls = [], 0, 0, 0.0, 0
    for model, u in sorted(usage["by_model"].items()):
        pin, pout = PRICES.get(model, (0.0, 0.0))
        cost = u["input_tokens"] / 1e6 * pin + u["output_tokens"] / 1e6 * pout
        rows.append((model, u, cost, pin, pout))
        tot_in += u["input_tokens"]; tot_out += u["output_tokens"]; tot_cost += cost; tot_calls += u["calls"]
    lines = [
        "# Usage report - final full-dataset run",
        "",
        f"Generated from `code/evaluation/out/run_report.json` of the run that wrote `output.csv` "
        f"({n} requests, runtime {rep['runtime_seconds']} s). The model caches were cleared before this run, "
        "so every token below was actually spent in it.",
        "",
        "Provider: **OpenAI** (Chat Completions API, structured JSON-schema output for intent).",
        "",
        "## Per model",
        "",
        "| Model | Purpose | Calls | Input tokens | Output tokens | Price in/out (USD per 1M) | Cost (USD) |",
        "|---|---|---:|---:|---:|---|---:|",
    ]
    for model, u, cost, pin, pout in rows:
        lines.append(f"| {model} | {PURPOSE.get(model, '-')} | {u['calls']} | {u['input_tokens']:,} | "
                     f"{u['output_tokens']:,} | {pin:.2f} / {pout:.2f} | {cost:.4f} |")
    lines += [
        f"| **Total** | | **{tot_calls}** | **{tot_in:,}** | **{tot_out:,}** | | **{tot_cost:.4f}** |",
        "",
        "## Per request",
        "",
        f"- Total tokens: {tot_in + tot_out:,} (input {tot_in:,}, output {tot_out:,})",
        f"- Average tokens per request: {(tot_in + tot_out) / max(n, 1):,.1f}",
        f"- Average model calls per request: {tot_calls / max(n, 1):.2f}",
        f"- Estimated total cost: ${tot_cost:.4f}",
        f"- Estimated cost per request: ${tot_cost / max(n, 1):.6f}",
        f"- Failed model calls (retried, then rule fallback): {usage['failures']}",
        f"- Cache hits during this run: {usage['cached_hits']}",
        "",
        "## Where the tokens go and why",
        "",
        "- Every deterministic stage (evidence parsing, identity graph, state reconstruction, policy, planning, "
        "serialization) uses **zero** model tokens.",
        "- Intent: one short prompt per distinct request text, sampled 3 times for a majority vote "
        "(the 32 samples contain no NOT_APPLICABLE example, so this step gets extra robustness).",
        "- Vision: one transcription per screenshot (11 images), cached by image SHA-256; identifiers are then "
        "extracted deterministically from the transcription.",
        "- Re-runs are free: results are cached by (model, prompt version, content hash).",
        "",
        "Prices: OpenAI model pages for GPT-5.4 mini ($0.75 / $4.50) and GPT-5.4 ($2.50 / $15.00) per 1M "
        "input/output tokens, standard tier. Costs are estimates computed from the token counts the API returned.",
    ]
    (HERE / "usage_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
