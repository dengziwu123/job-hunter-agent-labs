# Grounded Job Research Task

Keep every `{{placeholder}}` below. The course runtime fills them with the
current request, evidence, MCP declarations, and tool results.

Follow the reusable rules and answer the user's current request from the evidence
provided in this run.

Decide whether the request actually asks for current or available openings at a
company. If it does, use `list_openings` with arguments allowed by its declared
schema. Do not call a tool merely because it is available: evidence verification,
fit/gap analysis, resume drafting, outreach drafting, and interview preparation
do not require a job-board call unless the user also asks for current openings.

Compare each role requirement with candidate evidence. A requirement stated in
a job description or opening is a role fact, not proof that the candidate has
that qualification. When no candidate source supports a requirement, describe
it as a gap or missing information and do not rewrite it as candidate experience.

Return only a JSON array of short factual claim objects matching the supplied
schema. Each object must contain `claim` and `source_id`. Use the exact
`source_id` of a current evidence snippet only when that snippet supports the
claim. If no provided evidence supports the claim, use an empty `source_id` so
the verifier can record it as unsupported. Never invent a source identifier or
approve an external action.

## Reusable Skill rules

{{skill_rules}}

## User request

{{user_request}}

## Candidate and role evidence

{{evidence_sources}}

## Available MCP tools

{{available_tools}}

## Current-opening result references

The complete selected records appear in the evidence section above. These
references identify which MCP results survived the evidence budget.

{{job_openings}}
