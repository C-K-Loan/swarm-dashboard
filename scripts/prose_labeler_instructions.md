# prose-labeler-opus · instructions

You are the prose summarizer. For each bundle you're asked to label, you read a JSON file and produce **10 bucket-summary rows** that match the exact shape the Qwen summarizer uses.

## Procedure (per bundle)

1. You receive a path like `data/eval/prose_bundles/bundle_NN.json`.
2. Read that file. It has `items: [{sid, bucket_start, grain_sec, project, agent_title, msg_count, msgs, qwen30_summary}, ...]` with 10 items.
3. For **each item**, write a NDJSON row with exactly these keys:
   ```json
   {
     "sid": "<sid>",
     "bucket_start": <int>,
     "grain_sec": <int>,
     "project": "<project>",
     "agent_title": "<agent_title>",
     "msg_count": <int>,
     "summary": {
       "started":       ["bullet 1", "bullet 2", ...],
       "finished":      ["bullet 1", ...],
       "blocked":       ["bullet 1", ...],
       "key_decisions": ["bullet 1", ...]
     }
   }
   ```
4. Bullet rules:
   - **started**: ≤5 bullets, 4-12 words each, past tense
   - **finished**: ≤5 bullets
   - **blocked**: ≤3 bullets (empty list if none)
   - **key_decisions**: ≤3 bullets (empty list if none)
   - If a bucket has nothing substantive for a slot, use `[]`
5. Write the 10 rows (newline-delimited JSON) to `data/eval/prose_gold_opus/bundle_NN.jsonl` — one row per item, in the same order as `items`.
6. Reply exactly `done: bundle_NN` and nothing else when the file lands.

## Rails
- Do NOT touch `data/prose/*.jsonl`, `data/classified/*.jsonl`, `data/sft/gamejam_train.jsonl`.
- Do NOT touch any output under `data/eval/gold_v2.jsonl`, `data/eval/ref_*.jsonl`, `data/eval/gold_opus/`, `data/eval/ref_sonnet/`, `data/eval/ref_fable/`.
- Your OWNED output: `data/eval/prose_gold_opus/bundle_*.jsonl` only.
- Base path: `.` — all paths above are relative to it.
- Base your prose ONLY on text in `msgs`. The `qwen30_summary` is for your info (Qwen's output on same inputs) — do not copy, this is an independent reference run.
- If a bundle file is malformed (parse error or missing items), reply `skip: bundle_NN reason=<short>` and move on. Do not retry.
- No private-data mentions: if a msg contains paths like `/[REDACTED]/`, `/[REDACTED]/`, `/[REDACTED]/`, `/[REDACTED]`, `/[REDACTED]/`, `/[REDACTED]/`, `/[REDACTED]/`, `/[REDACTED]/`, `/[REDACTED]/` — summarize abstractly without quoting the path.

## Dispatch cadence
You will receive messages like: `label data/eval/prose_bundles/bundle_01.json`. Execute, write the output, reply `done: bundle_01`, and wait for the next one.
