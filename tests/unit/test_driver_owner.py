"""Unit tests for analyst_driver.owner — the run ownership record and probe.

The probe's three answers are the point: "alive" is proof and must refuse,
"dead" is proof of the opposite, and "unknown" (a record from another host)
must never be reported as dead.
"""

import json
import os
import socket
import subprocess

from analyst_driver.owner import (
    clear_owner,
    owner_path,
    probe_owner,
    read_owner,
    set_owner_job,
    write_owner,
)

# --------------------------------------------------------------- write / read


def test_write_then_read_round_trip(tmp_path):
    rec = write_owner(tmp_path, executor="local")
    back = read_owner(tmp_path)
    assert back == rec
    assert back["pid"] == os.getpid()
    assert back["host"] == socket.gethostname()
    assert back["job_id"] is None


def test_write_records_pid_start_on_linux(tmp_path):
    rec = write_owner(tmp_path, executor="local")
    # /proc exists on the supported platform; elsewhere None is the honest answer.
    if os.path.exists(f"/proc/{os.getpid()}/stat"):
        assert isinstance(rec["pid_start"], int)


def test_read_absent_is_none(tmp_path):
    assert read_owner(tmp_path) is None


def test_read_corrupt_is_none(tmp_path):
    owner_path(tmp_path).write_text("{ truncated")
    assert read_owner(tmp_path) is None


def test_clear_is_idempotent(tmp_path):
    write_owner(tmp_path, executor="local")
    clear_owner(tmp_path)
    clear_owner(tmp_path)
    assert read_owner(tmp_path) is None


def test_set_owner_job_keeps_the_pid(tmp_path):
    rec = write_owner(tmp_path, executor="slurm")
    updated = set_owner_job(tmp_path, "1884213")
    assert updated["job_id"] == "1884213"
    assert updated["pid"] == rec["pid"]
    assert read_owner(tmp_path)["job_id"] == "1884213"


def test_set_owner_job_without_a_record_is_none(tmp_path):
    assert set_owner_job(tmp_path, "1") is None


def test_write_is_atomic_no_tmp_left(tmp_path):
    write_owner(tmp_path, executor="local")
    assert not list(tmp_path.glob("*.tmp"))


# --------------------------------------------------------------------- probe


def test_probe_no_owner_is_free():
    assert probe_owner(None)["driver"] == "free"


def test_probe_own_process_is_self(tmp_path):
    rec = write_owner(tmp_path, executor="local")
    assert probe_owner(rec)["driver"] == "self"


def test_probe_foreign_host_is_unknown_never_dead():
    rec = {"host": "some-other-node", "pid": 1234, "pid_start": 99, "executor": "local"}
    probe = probe_owner(rec, this_host="corrino")
    assert probe["driver"] == "unknown"
    assert "another host" in probe["detail"]


def test_probe_live_foreign_pid_on_this_host_is_alive(tmp_path):
    proc = subprocess.Popen(["sleep", "30"])
    try:
        rec = write_owner(tmp_path, executor="local", pid=proc.pid)
        assert probe_owner(rec)["driver"] == "alive"
    finally:
        proc.kill()
        proc.wait()


def test_probe_dead_pid_is_dead(tmp_path):
    proc = subprocess.Popen(["true"])
    rec = write_owner(tmp_path, executor="local", pid=proc.pid)
    proc.wait()
    # The pid is reaped, so it no longer exists.
    assert probe_owner(rec)["driver"] == "dead"


def test_probe_recycled_pid_is_dead_not_alive():
    """A recycled pid must not resurrect a crashed driver.

    Same pid, different start time — without the start-time check this run
    could never be resumed.
    """
    rec = {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "pid_start": -1,  # cannot match any real start time
        "executor": "local",
    }
    probe = probe_owner(rec, this_pid=os.getpid() + 1)
    assert probe["driver"] == "dead"
    assert "recycled" in probe["detail"]


def test_probe_missing_pid_is_dead():
    assert probe_owner({"host": socket.gethostname()})["driver"] == "dead"


def test_probe_job_reported_separately_from_driver():
    """A SLURM job outliving its driver is normal, not an error."""
    rec = {"host": "other", "pid": 5, "executor": "slurm", "job_id": "77"}
    probe = probe_owner(rec, job_state="running", this_host="corrino")
    assert probe["driver"] == "unknown"
    assert probe["job"] == "alive"


def test_probe_job_none_when_no_job_id():
    rec = {"host": socket.gethostname(), "pid": os.getpid(), "executor": "local"}
    assert probe_owner(rec)["job"] == "none"


def test_probe_job_unknown_when_state_not_asked():
    rec = {"host": "other", "pid": 5, "executor": "slurm", "job_id": "77"}
    assert probe_owner(rec, this_host="corrino")["job"] == "unknown"


def test_probe_job_dead_on_terminal_state():
    rec = {"host": "other", "pid": 5, "executor": "slurm", "job_id": "77"}
    assert probe_owner(rec, job_state="failed", this_host="corrino")["job"] == "dead"


def test_owner_file_is_json_a_human_can_read(tmp_path):
    write_owner(tmp_path, executor="slurm", job_id="42")
    rec = json.loads(owner_path(tmp_path).read_text())
    assert rec["executor"] == "slurm" and rec["job_id"] == "42"
