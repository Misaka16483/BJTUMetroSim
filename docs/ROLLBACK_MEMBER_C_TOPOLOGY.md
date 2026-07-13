# Member C Phase 2 topology baseline

This document records the known-good checkpoint created before further
experiments on the Member C interlocking topology demo.

- Baseline branch: `backup/member-c-topology-baseline-20260710`
- Baseline tag: `member-c-topology-baseline-20260710`
- Scope: the complete working tree as it existed on 2026-07-10, including
  the Phase 2 interlocking implementation and the topology demo.

## Restore this baseline

To discard later local experiments and return the current branch to this
checkpoint:

```powershell
git reset --hard backup/member-c-topology-baseline-20260710
```

To inspect or continue from the preserved version without changing another
branch:

```powershell
git switch backup/member-c-topology-baseline-20260710
```

The branch and tag should remain unchanged. Create experimental branches from
this checkpoint rather than committing new work directly to it.
