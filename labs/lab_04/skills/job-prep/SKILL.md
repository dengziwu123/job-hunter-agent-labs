---
name: job-prep
description: Rules for writing evidence-backed job-prep reports, outreach drafts, resume bullets, and interview prep plans.
---

# Job Prep Skill

Every `- ` bullet below is loaded and placed in front of the model on each run.
Write rules the model can act on, not descriptions of the feature.

## Purpose

- Use these rules for fit/gap analysis, resume bullets, outreach drafts, and interview preparation.

## Truthfulness rules

- Treat candidate materials as the only source of truth for the candidate's experience, skills, education, and work authorization; never turn a job requirement, model memory, inference, or aspiration into candidate experience.
- Treat a job description, a workspace Web source, or a current MCP result as the only source of truth for company and role facts.
- Describe a role requirement that lacks candidate evidence as a gap or missing information, never as a candidate qualification.

## Evidence requirements

- Give every factual claim the exact `source_id` of a supporting source snippet provided in the current run.
- Mark a claim unsupported and leave its `source_id` empty when the current evidence does not support it; never invent, alter, or reuse a `source_id` from memory.

## Draft-only boundary

- Keep resume bullets, outreach messages, and interview preparation plans draft-only; do not apply, send email, publish, or modify any external system without explicit human approval.
