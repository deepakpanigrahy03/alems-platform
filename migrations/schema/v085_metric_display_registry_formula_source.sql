-- v085: Add formula_latex, source_description to metric_display_registry
-- Extracted from v084 which was modified after being applied.
ALTER TABLE metric_display_registry ADD COLUMN formula_latex      TEXT;
ALTER TABLE metric_display_registry ADD COLUMN source_description TEXT;
