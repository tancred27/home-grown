-- The lightweight map: one plain-language "what is this for" line per unit.
--
-- Written by an LLM from the unit's OWN source text (name, manifest description, README),
-- never from a query. Measured value: adding these lines lifted held-out gate-1 recall@5
-- from 0.82 to 0.98 (experiments/gate1/RESULTS.md run 4b). It is derived from the
-- source, so it inherits the asset's visibility like every other column here.

alter table asset add column if not exists purpose text;
alter table asset add column if not exists purpose_model text;          -- who wrote it
alter table asset add column if not exists purpose_at timestamptz;
alter table asset add column if not exists purpose_evidence_id uuid references evidence(id);
alter table asset add column if not exists purpose_confidence text;     -- high|medium|low
