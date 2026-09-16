# Deployment migrations

One-time steps to run, in order, before running the app on this codebase. Covers two
pending sets of changes: the `page_id` format change + ingestion work from the
previous commit (`00139cd`), and the version-history / deleted-page-archival /
bundle-pinning feature added since.

Respects `DB_BACKEND` (`mongodb` or `postgres`) via `.env`, same as the app itself.
Every script below supports a dry run (no args) and `--apply` to actually write.
Run dry-run first, read the output, then `--apply`.

## 0. If you're upgrading a live DB (not a fresh/empty one)

Everything below still applies in the same order, but treat these as hard
requirements rather than suggestions once real data is involved:

- **Back this up first.** `mongodump` / `pg_dump` before touching anything with `--apply`.
- **Stop the app (or at least writes) for the duration of steps 1–4.** Pages
  being created/edited while `migrate_page_ids.py` is renaming ids, or while
  the version-creation hook is writing snapshots mid-rename, can race. Treat
  this as a maintenance window.
- **On Postgres, migrate before deploying the new code, not after.** The new
  app code reads/writes `pages.current_version_number` and the new
  `page_versions`/`deleted_pages*` tables — if that code runs against a DB
  still on migration 001, every page read/write fails with a missing-column
  error. Order: stop app → run steps 1–4 on the old code's DB → deploy new
  code → start app. (Mongo is more forgiving since missing fields just
  default, but keep the same order anyway.)
- **`migrate_page_ids.py` is not idempotent — do not run `--apply` more than
  once.** It generates a fresh random suffix for every page on every run,
  with no check for "already in the new format." A second run reshuffles
  already-migrated pages again, silently breaking anything that pinned to the
  first run's output (bundles, links shared with partners, cached LLM
  tool-call ids). If you're not sure whether it already ran against this DB,
  do the dry run first and eyeball whether the printed mapping looks like
  real renames or a no-op.
- Bundles are covered by `migrate_page_ids.py` now (it rewrites
  `bundles.entries[].page_id` alongside everything else), so no separate manual
  bundle check is needed even though this DB predates `1395d49`/`00139cd` and
  likely already has real bundles.

## 1. Schema — `python scripts/init_db.py`

Idempotent, safe to re-run. For Postgres this runs `alembic upgrade head`, which
now includes migration `002_page_versions` (adds `pages.current_version_number`
plus the `page_versions`, `deleted_pages`, `deleted_page_versions`,
`deleted_page_revisions` tables). For Mongo this (re)creates indexes, including
new ones for `page_versions` and `deleted_pages`.

```bash
python scripts/init_db.py
```

## 2. Page ID format — `python scripts/migrate_page_ids.py --apply`

From the previous commit: reassigns every page's `page_id` from the old
`page_id == title` scheme to the new Slack-style `{slug}-{suffix}` scheme, and
rewrites every reference to the old id it knows about (embedded `references`,
`page_refs`, `page_revisions`, `requests`, and `bundles.entries[].page_id`).

**Must run before steps 3 and 4** — both of the scripts below key off the
*final* `page_id`, and version ids embed `page_id` (`{page_id}-v{n}`).

```bash
python scripts/migrate_page_ids.py            # dry run — review the mapping first
python scripts/migrate_page_ids.py --apply
```

## 3. Backfill v1 versions — `python scripts/backfill_page_versions.py --apply`

New in this feature: every page created before version history shipped has zero
rows in `page_versions`. This synthesizes a `v1` snapshot from each published
page's current live state, so the history UI isn't empty and step 4 has
something to pin to.

```bash
python scripts/backfill_page_versions.py            # dry run
python scripts/backfill_page_versions.py --apply
```

## 4. Pin existing bundles — `python scripts/migrate_bundle_pins.py --apply`

New in this feature: stamps every existing bundle entry's `version_id` to its
page's current latest version, so pre-existing bundles become frozen/reproducible
immediately instead of silently staying on "always latest." Entries that already
carry a `version_id` are left untouched. Depends on step 3 having run first —
entries for a page with no version yet are skipped and reported, not silently
left unpinned.

```bash
python scripts/migrate_bundle_pins.py            # dry run
python scripts/migrate_bundle_pins.py --apply
```

## Order summary

```
1. python scripts/init_db.py
2. python scripts/migrate_page_ids.py --apply
3. python scripts/backfill_page_versions.py --apply
4. python scripts/migrate_bundle_pins.py --apply
```

After this, start the app as usual (`uvicorn app.main:app ...` / `streamlit run ...`).
None of these four scripts need to run again on subsequent deploys — they're
one-time data migrations, not part of the normal boot sequence (`init_db.py` is
the only one safe/expected to re-run).
