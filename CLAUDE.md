Read docs/SPEC.md before any task. Follow Section 0 rules.

## Database — critical

`nudgeai` (on the `primetwok8-testing…` RDS host) is **live production** and the voice agent writes to it all day.
Never run tests, `alembic downgrade`, `alembic stamp base`, or anything that creates or drops tables against it.
Tests use a disposable local MySQL/MariaDB.

## Keep the trainer guide current

`docs/TRAINER_GUIDE.md` (in the dashboard repo) is the user documentation, and the dashboard's **Help & FAQ**
page shows it as built. Any change users can see (a page, a filter, a button, a message, a rule) updates the
guide in the same change, including the FAQ when it answers a new question. `tests/lib/guide.test.ts` fails if a
sidebar page isn't described in it.
