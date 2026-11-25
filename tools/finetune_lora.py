#!/usr/bin/env python3
"""LoRA fine-tune an open-weight model on your own C.A.R.L.A. conversations.

Run it where there is a GPU — free Google Colab / Kaggle notebooks work —
not on a small PC. Steps:

    1. On your PC:   carla export --format training -o my_chats.jsonl
    2. Upload my_chats.jsonl to the notebook, then:
           pip install "transformers>=4.44" "peft>=0.12" "trl>=0.11" datasets accelerate
           python finetune_lora.py --data my_chats.jsonl --base Qwen/Qwen2.5-1.5B-Instruct
    3. Convert for C.A.R.L.A.'s engine (llama.cpp's own script, see docs/FINETUNE.md):
           python convert_hf_to_gguf.py out/merged --outfile mine.gguf --outtype q8_0
       then put the .gguf in the Model Manager's "Custom model" / models folder.

`--dry-run` only checks and summarises the data (no GPU, no heavy imports).

NOTE: the training path was written against current transformers/peft/trl
APIs but has NOT been run in the development sandbox (no GPU, no network
to model hubs). The data checks below are unit-tested.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_examples(path: str | Path) -> list[dict]:
    """Read and validate chat-format JSONL. Raises ValueError with a line number."""
    rows: list[dict] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {n}: not valid JSON ({exc})") from exc
        msgs = row.get("messages") if isinstance(row, dict) else None
        if not isinstance(msgs, list) or not msgs:
            raise ValueError(f"line {n}: expected {{\"messages\": [...]}}")
        for m in msgs:
            if m.get("role") not in ("system", "user", "assistant") or not str(m.get("content", "")).strip():
                raise ValueError(f"line {n}: each message needs a role and non-empty content")
        if not any(m["role"] == "assistant" for m in msgs):
            raise ValueError(f"line {n}: no assistant reply to learn from")
        rows.append(row)
    if not rows:
        raise ValueError("the file has no examples")
    return rows


def summarise(rows: list[dict]) -> str:
    turns = sum(1 for r in rows for m in r["messages"] if m["role"] == "assistant")
    chars = sum(len(m["content"]) for r in rows for m in r["messages"])
    note = "" if len(rows) >= 200 else (
        "\n  Note: fewer than ~200 conversations will mostly teach style, not knowledge.")
    return f"{len(rows)} conversations, {turns} assistant replies, ~{chars // 4:,} tokens{note}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="JSONL from `carla export --format training`")
    ap.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct", help="Hugging Face model to start from")
    ap.add_argument("--out", default="out", help="output folder")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16, help="LoRA rank")
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true", help="validate the data and exit")
    args = ap.parse_args(argv)

    try:
        rows = load_examples(args.data)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(summarise(rows))
    if args.dry_run:
        return 0

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:
        print(f"error: missing {exc.name}. Install: pip install transformers peft trl datasets accelerate",
              file=sys.stderr)
        return 3

    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForCausalLM.from_pretrained(
        args.base, torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32)
    trainer = SFTTrainer(
        model=model,
        args=SFTConfig(output_dir=args.out, num_train_epochs=args.epochs, learning_rate=args.lr,
                       per_device_train_batch_size=args.batch, gradient_accumulation_steps=4,
                       max_length=args.max_len, logging_steps=10, save_strategy="no",
                       bf16=torch.cuda.is_available(), report_to=[]),
        train_dataset=Dataset.from_list(rows),
        peft_config=LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05,
                               target_modules="all-linear", task_type="CAUSAL_LM"),
        processing_class=tok,
    )
    trainer.train()
    merged = trainer.model.merge_and_unload()
    out = Path(args.out) / "merged"
    merged.save_pretrained(out)
    tok.save_pretrained(out)
    print(f"\nDone. Merged model: {out}\nNext: convert to GGUF with llama.cpp (see docs/FINETUNE.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
