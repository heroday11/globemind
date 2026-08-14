# Runtime logs boundary

Status: current runtime boundary
Scope: local operational output, not source or test fixtures

This directory is a compatibility location for local logs, PID metadata and
controller state. Contents are ignored and must not be committed. Do not read
secrets from logs, infer process identity from a PID alone, or remove/rotate
files while a pipeline is running. Use the read-only runtime catalog and its
runbook for diagnostics.

The future external path is recorded, but not activated, in
[`../quality/runtime-path-policy.json`](../quality/runtime-path-policy.json).
