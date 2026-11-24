-- v126: llm_interactions.ttft_null_reason
-- Root cause: when streaming failed or no token arrived, ttft_ms was filled with
-- the full round trip or the total time, a value that was never measured.
-- ttft_ms is now NULL in those cases and this column says why:
-- stream_failed_fallback, zero_tokens, call_failed. NULL when ttft_ms is measured.
-- Applies to: ALL platforms. Existing rows stay NULL.
ALTER TABLE llm_interactions ADD COLUMN ttft_null_reason TEXT;
