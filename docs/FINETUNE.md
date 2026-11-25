# Training a model on your own conversations

C.A.R.L.A. exports your chats as a training file; `tools/finetune_lora.py`
fine-tunes a small open model on them with LoRA; llama.cpp converts the result
to GGUF so C.A.R.L.A.'s built-in engine can run it. **Free**, but it needs a GPU,
so use a free notebook (Google Colab, Kaggle) rather than the OptiPlex.

> Status: the data export and the data checks are tested. The training and
> conversion steps were written against current library APIs but **have not
> been run yet** — expect to adjust versions on first use.

## What to expect
- A LoRA run teaches **style and habits** (tone, formats, your projects' vocabulary).
  It does not reliably add facts. A few hundred good conversations is a sensible start.
- Start from a model whose licence allows it. A 1.5B–3B base is realistic on a free GPU.
- If you want it uncensored, start from an already-uncensored base — fine-tuning on
  chats that contain refusals will bring refusals back.

## Steps
1. **Export** on your PC: `carla export --format training -o my_chats.jsonl`
   (one `{"messages": [...]}` per line; tool chatter is left out).
2. **Check**: `python tools/finetune_lora.py --data my_chats.jsonl --dry-run`
3. **Train** in a notebook with a GPU:
   ```bash
   pip install "transformers>=4.44" "peft>=0.12" "trl>=0.11" datasets accelerate
   python finetune_lora.py --data my_chats.jsonl --base Qwen/Qwen2.5-1.5B-Instruct
   ```
4. **Convert** (llama.cpp's script, from a llama.cpp checkout):
   ```bash
   pip install -r requirements.txt
   python convert_hf_to_gguf.py out/merged --outfile mine.gguf --outtype q8_0
   ```
5. **Use it**: copy `mine.gguf` into your C.A.R.L.A. `models` folder, then in the
   Model Manager pick it under *Installed* → **Use this** → **Start**.
