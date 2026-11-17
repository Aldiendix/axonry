# Initial champion (bit-exact variant): the edited model of "Sparks of Weight-Space Programming" (Experiment 8)

Same model as `../loop-ablation/REPORT.md` (Qwen3.5-0.8B edited by GPT-6 Astra; released checkpoint
`RikiyaT/Qwen3.5-0.8B-weight-space-programming` @ `d1671a1`; https://rikiyat.github.io/blog/weight-space-programming/).

**What was changed.** The base loops until the token limit on almost every math problem; Astra removed one loop
direction (alpha 6) from 64 output columns of `mlp.down_proj` in layers 15 and 19 (131,072 weights). This spec writes
those 64 edited columns of each tensor (`set_cols`, bfloat16 values stored as float32) on base `qwen35-0.8b-think`
(Qwen/Qwen3.5-0.8B @ 2fc0636). Rebuilt tensors are bit-identical to the released checkpoint (sha256 layer 15
`82e2d733...ee53`, layer 19 `8ef3c61b...2eb2`, as published). Payload 513 KB; relative change 0.106 / 0.081. Needs a
rules version whose ops include `set_cols` (competition repo commit `0d48be4` or later).

**Results (from the paper / its release, thinking on, 32k tokens).** MATH-500 7.9% -> 48.1% (independent re-run of the
checkpoint, 500 x 4: 51.0%), GSM8K 13.8% -> 67.1%, IFEval 21.0% -> 62.3%, token-limit hits 93.0% -> 33.1%.
