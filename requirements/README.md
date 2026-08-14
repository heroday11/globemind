# Dependency roles

Status: current dependency-role policy
Scope: source inputs, lock files and runtime installation provenance

Dependency inputs and locks are grouped by runtime role. A role lock is an
attested installation input, not a general-purpose development environment.

- `roles/web.in` is the reviewed direct-input set for the Web/API role.
- `roles/web.lock` is the hash-pinned resolved lock consumed by runtime build
  tooling.
- `roles/web.lock.metadata.json` records the resolver, Python implementation,
  input digest, lock digest and generation timestamp.

When changing a role input, regenerate its lock with the documented resolver,
review the diff for transitive changes and refresh the metadata digest as one
change. Do not hand-edit generated lock sections or copy packages from a
running/release environment. Heavy research/GPU dependencies remain in the
root requirements files unless they are intentionally promoted to a role.

For a read-only local inspection, use the repository Python runtime and avoid
release directories:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pip check
```

Production runtime construction and attestation are documented in
[`docs/operations/PYTHON_RUNTIME.md`](../docs/operations/PYTHON_RUNTIME.md).
