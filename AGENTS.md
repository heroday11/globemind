# GlobeMind Repository Safety Rules

These rules apply to every automated or manual engineering session in this
repository.

## Production release boundary

- Never run or import Python from `/root/data/releases/globemind/current`, a
  versioned release directory, `previous`, or `rejected`.
- Never add a production release or its `backend` directory to `PYTHONPATH` or
  `sys.path`. Use HTTP/control-plane diagnostics, or copy the artifact to an
  isolated temporary directory first.
- Start every Python diagnostic with both
  `PYTHONDONTWRITEBYTECODE=1` and `-B`. Setting the environment variable after
  the interpreter has started is too late.
- Treat release directories as evidence. Do not remove unexpected files in
  place. Record them, reconstruct a clean copy from `SHA256SUMS`, verify it,
  and use an atomic promotion procedure.
- Run the production verifier before and after any release-adjacent
  diagnostic. A release must contain no unlisted files, Python bytecode, or
  cache directories.

## Running services and pipelines

- Do not signal a PID based only on a PID file or command-name match. Verify
  process start ticks, boot ID, executable, working directory, and controller
  ownership first.
- Do not print process arguments, environments, database URLs, tokens, or
  secret-file contents. Use the redacted runtime control interface.
- Do not stop, restart, adopt, or migrate a long-running pipeline without a
  checkpoint, replay proof, rollback procedure, and an explicit maintenance
  step in the applicable runbook.
- Keep operational state, logs, sockets, and secrets outside immutable release
  directories.

## Change discipline

- Work in the repository or isolated staging paths, never in a deployed
  artifact.
- Preserve unrelated worktree changes. Do not reset or rewrite user-owned
  changes to make a gate pass.
- Use the role-specific locked Python runtime and the repository quality gates
  for release validation.
