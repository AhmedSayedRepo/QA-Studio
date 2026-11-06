-- Generator test style for remote runs: 'manual' (default) or 'automation'.
-- The worker calls engine.set_case_style(run.case_style).
ALTER TABLE public.remote_runs
    ADD COLUMN IF NOT EXISTS case_style text NOT NULL DEFAULT 'manual';

ALTER TABLE public.remote_runs
    DROP CONSTRAINT IF EXISTS remote_runs_case_style_check;
ALTER TABLE public.remote_runs
    ADD CONSTRAINT remote_runs_case_style_check
    CHECK (case_style = ANY (ARRAY['manual'::text, 'automation'::text]));
