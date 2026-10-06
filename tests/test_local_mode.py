import json
import multiprocessing as mp
import os
import time

import pytest

import state
import local_runner


def _use(monkeypatch, tmp_path):
    f = str(tmp_path / "s.json")
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", f)
    monkeypatch.setattr(state, "backend", lambda: "local_json")
    return f


def _writer(path, prefix, n):
    state.LOCAL_STATE_FILE = path
    state.backend = lambda: "local_json"
    for i in range(n):
        state.set_value(f"{prefix}:{i}", {"i": i})


def test_many_processes_lose_no_keys(monkeypatch, tmp_path):
    f = _use(monkeypatch, tmp_path)
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_writer, args=(f, f"p{k}", 25)) for k in range(4)]
    [p.start() for p in procs]
    [p.join(60) for p in procs]
    assert all(p.exitcode == 0 for p in procs)
    data = json.load(open(f))
    assert len(data) == 100                      # 4 processes x 25 keys, nothing overwritten or lost


def test_reads_are_private_copies_and_see_other_writers(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path)
    state.set_value("k", [1, 2])
    v = state.get_value("k")
    v.append(99)                                 # mutating the returned list must not touch stored state
    assert state.get_value("k") == [1, 2]
    # another process rewrites the file -> this process must notice
    other = {"k": [7], "z": 1}
    time.sleep(0.01)
    with open(state.LOCAL_STATE_FILE, "w") as fh:
        json.dump(other, fh)
    assert state.get_value("k") == [7] and state.get_values(["k", "z"]) == {"k": [7], "z": 1}


def test_corrupt_file_reads_empty_and_recovers(monkeypatch, tmp_path):
    f = _use(monkeypatch, tmp_path)
    open(f, "w").write("{not json")
    assert state.get_value("x") is None
    assert state.set_value("x", 5) and state.get_value("x") == 5


def test_local_lock_is_exclusive_until_expiry(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path)
    assert state.acquire_lock("L", 60, "a") is True
    assert state.acquire_lock("L", 60, "b") is False
    state.release_lock("L")
    assert state.acquire_lock("L", 60, "b") is True
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)       # expired
    assert state.acquire_lock("L", 60, "c") is True


def test_no_temp_files_left_behind(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path)
    for i in range(10):
        state.set_value(f"k{i}", i)
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


def test_runner_schedule_and_due_order(monkeypatch):
    monkeypatch.delenv("LOCAL_RUNNER_MADEONSOL", raising=False)
    jobs = local_runner.schedule()
    assert [j[0] for j in jobs] == ["poll-fast", "poll-slow", "poll-madeonsol"]
    assert [j[0] for j in local_runner.due_jobs({}, 10_000, jobs)] and len(local_runner.due_jobs({}, 10_000, jobs)) == 3
    last = {"poll-fast": 10_000 - 60, "poll-slow": 10_000 - 1300, "poll-madeonsol": 10_000 - 100}
    assert [j[0] for j in local_runner.due_jobs(last, 10_000, jobs)] == ["poll-slow"]
    monkeypatch.setenv("LOCAL_RUNNER_MADEONSOL", "false")
    assert [j[0] for j in local_runner.schedule()] == ["poll-fast", "poll-slow"]


def test_actions_runner_skips_without_upstash(monkeypatch, capsys):
    import scheduler
    monkeypatch.setattr(scheduler, "IS_GITHUB_ACTIONS", True)
    monkeypatch.setattr(scheduler.CONFIG, "upstash_redis_rest_url", None)
    assert scheduler._skip_local_state_on_actions("poll-fast") is True
    monkeypatch.setattr(scheduler, "IS_GITHUB_ACTIONS", False)
    assert scheduler._skip_local_state_on_actions("poll-fast") is False
