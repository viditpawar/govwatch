-- share of the summary's content words found in the source text (agent/faithfulness.py).
-- too noisy to judge one summary by; its median over a window is the grounding signal
ALTER TABLE bill_summaries ADD COLUMN source_support real;
