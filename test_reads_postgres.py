"""Postgres read-tracking tests (P7.3 backend).

Skipped unless TEST_DATABASE_URL points at a reachable Postgres, e.g.:

    docker run -d --name bible-pg -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=reads -p 15432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:test@localhost:15432/reads \\
        .venv/bin/python -m pytest test_reads_postgres.py -v

Each test runs dash_app in a SUBPROCESS rather than importing it here. That is
not incidental: dash_app resolves its backend at import time (USE_POSTGRES, _PH,
_TS are module-level), and test_dash_app.py has usually already imported it
under SQLite in the same session. Re-importing in-process would either get the
cached SQLite module or, if reloaded, swap the backend out from under any test
that runs afterwards. A subprocess gives each case a clean interpreter.
"""
import csv
import os
import subprocess
import sys
import tempfile
import textwrap

import pytest

pytestmark = pytest.mark.postgres

DSN = os.environ.get("TEST_DATABASE_URL", "")

if not DSN:
    pytest.skip("TEST_DATABASE_URL not set", allow_module_level=True)


def _run(body, dsn=None):
    """Execute `body` in a subprocess with dash_app configured for Postgres."""
    tmp = tempfile.mkdtemp()
    csv_path = os.path.join(tmp, "graded.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ref", "verse", "comprehension_rate", "known_count", "total_count"])
        w.writerow(["Gen 1:1", "in the beginning", 0.9, 9, 10])

    prelude = textwrap.dedent(
        f"""
        import logging, os
        os.environ["BIBLE_GRADED_CSV"] = {csv_path!r}
        os.environ["DATABASE_URL"] = {(dsn or DSN)!r}
        logging.disable(logging.WARNING)
        import dash_app as da
        assert da.USE_POSTGRES, "expected the Postgres backend"
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(body)],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return proc.stdout


def test_backend_selected():
    out = _run('print(da._PH, da._TS)')
    assert "%s TIMESTAMPTZ" in out


def test_reads_round_trip():
    _run(
        """
        da._mark_read("pg-round-trip", ["Gen 1:1", "Gen 1:2"])
        assert da.get_read_refs("pg-round-trip") == {"Gen 1:1", "Gen 1:2"}
        da._mark_unread("pg-round-trip", ["Gen 1:1"])
        assert da.get_read_refs("pg-round-trip") == {"Gen 1:2"}
        da._mark_unread("pg-round-trip", ["Gen 1:2"])
        assert da.get_read_refs("pg-round-trip") == set()
        """
    )


def test_mark_read_idempotent():
    """The ON CONFLICT DO NOTHING path -- a re-mark must not raise or duplicate."""
    _run(
        """
        da._mark_read("pg-idempotent", ["Gen 1:1"])
        da._mark_read("pg-idempotent", ["Gen 1:1"])
        assert da.get_read_refs("pg-idempotent") == {"Gen 1:1"}
        da._mark_unread("pg-idempotent", ["Gen 1:1"])
        """
    )


def test_reads_scoped_per_bible():
    _run(
        """
        da._mark_read("pg-bible-a", ["Gen 1:1"])
        assert da.get_read_refs("pg-bible-b") == set()
        da._mark_unread("pg-bible-a", ["Gen 1:1"])
        """
    )


def test_concurrent_writers():
    """The reason this backend exists.

    Eight simultaneous writers is the workload SQLite on a shared volume cannot
    serve -- it allows exactly one writer, and over NFS its advisory locking is
    unreliable enough to surface as "database is locked". If this ever regresses
    to SQLite, this test fails.
    """
    _run(
        """
        import concurrent.futures as cf
        def writer(n):
            da._mark_read("pg-concurrent", [f"Ref {i}" for i in range(n*20, n*20+20)])
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(writer, range(8)))
        got = da.get_read_refs("pg-concurrent")
        assert len(got) == 160, f"expected 160 refs, got {len(got)}"
        da._mark_unread("pg-concurrent", list(got))
        """
    )


def test_degrades_when_database_unreachable():
    """Read tracking is a convenience; losing it must not take the reader down.

    Port 15499 has nothing listening, which is what a rollout looks like from
    the app's side while Postgres is restarting.
    """
    _run(
        """
        assert da.get_read_refs("nasb") == set()
        da._mark_read("nasb", ["Gen 1:1"])
        da._mark_unread("nasb", ["Gen 1:1"])
        print("degraded cleanly")
        """,
        dsn="postgresql://postgres:test@localhost:15499/reads",
    )
