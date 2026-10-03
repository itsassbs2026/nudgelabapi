Read docs/SPEC.md before any task. Follow Section 0 rules.

## Database — critical

`nudgeai` (on the `primetwok8-testing…` RDS host) is **live production** and the voice agent writes to it all day.
Never run tests, `alembic downgrade`, `alembic stamp base`, or anything that creates or drops tables against it.
Tests use a disposable local MySQL/MariaDB.
