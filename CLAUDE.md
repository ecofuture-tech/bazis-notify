# bazis-notify

Stored notifications of the users for Bazis: `notify(users, type, title, body, ...)` stores
a `Notification` per recipient with the texts in his language (`bazis.contrib.notify.service`),
the types are registered by the apps (`<app>/notifications.py`, `registry.register`), the
user reads and deletes his own notifications through `bazis.contrib.notify.router`, the new
ones are published after the commit over bazis-ws (optional) and sent by e-mail through the
mail backend of the core (`email=True`).

Security: the API shows a user only his own notifications (`restrict_queryset`) and refuses
anonymous requests (403); `metadata` drops the keys that look like secrets, since the user
and the socket see it. A notification is immutable except `read_at` (`validate_item`).

The package code is in `bazis/contrib/notify`, the sample project used by the tests is in
`sample/` (a user model with `UserLanguageMixin` and `UserWsMixin`), the tests are in `tests/`.

## Running the tests

The tests need PostgreSQL with PostGIS and Redis (see `.github/workflows/tests.yml`).
Run them from the `sample` directory:

```bash
cd sample
BS_DEBUG=true \
BS_SECRET_KEY=local-secret-key-that-is-long-enough-0123456789 \
BS_DATABASES__DEFAULT__HOST=localhost BS_DATABASES__DEFAULT__PORT=5432 \
BS_DATABASES__DEFAULT__NAME=bazis BS_DATABASES__DEFAULT__USER=postgres \
BS_DATABASES__DEFAULT__PASSWORD=postgres \
BS_CACHES__DEFAULT__LOCATION=redis://localhost:6379/1 \
BS_MEDIA_ROOT=/tmp/bazis/media BS_STATIC_ROOT=/tmp/bazis/static BS_WEBAPP_ROOT=/tmp/bazis/webapp \
python -m pytest ../tests -o addopts="" -p no:cacheprovider
```

Lint: `ruff check bazis tests sample`. CI also runs `python manage.py makemigrations --check
--dry-run` in `sample`: commit the migrations of model changes, including the sample apps.

## Releasing

A release is the tag `vX.Y.Z` on `main`: the Build and Publish workflow builds the package
(the version comes from the tag through setuptools-scm) and publishes it to PyPI
(pre-releases `-alphaN`/`-betaN`/`-rcN` go to Test PyPI) and creates the GitHub release.

Claude Code sessions cannot push tags. Release through the **Release** workflow instead:

1. Make sure the changes are merged into `main` and the Tests workflow is green on the
   `main` head commit (the Release workflow checks this and refuses otherwise).
2. Add the release notes as `docs/releases/X.Y.Z.md` in the change being released.
3. Start the workflow `release.yml` on `ref: main` with the input `version: X.Y.Z`
   (GitHub API: `POST /repos/ecofuture-tech/bazis-notify/actions/workflows/release.yml/dispatches`;
   with the GitHub MCP tools: `actions_run_trigger`, method `run_workflow`).
4. The Release run creates the annotated tag and starts Build and Publish on it. Check
   that both runs succeed and that the version appears on https://pypi.org/project/bazis-notify/.

Release the Bazis packages in dependency order: a package is tested in CI against the
versions of its Bazis dependencies published on PyPI. Pick the version by semver:
breaking changes (settings renamed or required, dependency removed, behavior changed)
bump the minor version while the project is below 3.0.
