# Releasing

Releases use the manually dispatched `.github/workflows/release.yml` workflow
from `main`. Its `pypi` environment controls publishing approval and the wait
timer. The workflow publishes the wheel and source distribution, then creates
the GitHub release and version tag at the workflow's commit SHA.

## Dependency release order

Publish safe-fs-ops before a Prescribe release that raises its minimum version.
For Prescribe 0.5.1, safe-fs-ops 0.1.1 must exist on PyPI before finalizing the
registry lockfile:

```console
uv lock --upgrade-package safe-fs-ops --refresh-package safe-fs-ops
uv sync --locked
uv run pytest -q
uv run mypy src
uv build
```

Review and commit `uv.lock` after this refresh. Check that safe-fs-ops resolves
from PyPI and satisfies the declared minimum, with no local wheel or checkout
paths in the committed metadata. The package-specific exclusion in `uv.toml`
allows this first-party release to be consumed immediately; the general
dependency age policy remains in effect.

A locally built dependency wheel can validate integration before publication,
but does not replace the final registry lock refresh. Refresh the package index
when consuming a newly published version so cached metadata does not hide it.
Do not use a frozen lockfile to validate the final published dependency combination.

## Publication checks

Before dispatch, run the relevant tests and confirm the version is new on PyPI
and its tag is unused. Tests are not enforced by the publishing workflow.
After environment approval, verify both uploaded distributions, a fresh
installation, and that the release tag points to the dispatched commit.

If package upload succeeds but GitHub release creation fails, inspect the
existing PyPI release before retrying. Finish the GitHub release for the same
commit rather than attempting to replace an already uploaded package.
