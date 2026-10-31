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


def test_local_runner_loads_dotenv_at_import(tmp_path):
    pytest.importorskip("dotenv", reason="python-dotenv not installed in this test environment -- "
                        "this exact gap (dotenv present on the real deployment machine but this "
                        "test's own interpreter lacking it) is what let the real bug go unverified "
                        "earlier; skip rather than report a misleading pass/fail here. Confirmed "
                        "installed (1.2.3) on Ali's real Windows Python via `pip show python-dotenv`.")
    """1 Nov 2026 (Ali): REAL bug found live -- local_runner.py never called load_dotenv() at all,
    unlike every other entry-point in this repo (worker_early_watch.py, dashboard.py, backtest.py).
    early_enabled() reads os.environ directly in THIS process at main() startup, before
    worker_early_watch.py is even spawned -- with .env never loaded, LOCAL_RUNNER_EARLY=false in
    .env silently had no effect and the supervisor kept launching the solana watcher anyway.
    Confirmed from live logs: the 31 Oct 21:17:45 PKT restart (meant to disable solana) re-started
    early-watch at that exact same timestamp, and solana kept trading for hours afterward.

    This test proves the fix by actually spawning a fresh subprocess (import-time load_dotenv()
    effects can't be tested by importing the already-loaded module in-process) in an isolated cwd
    with its own .env, and checking that LOCAL_RUNNER_EARLY from that .env is visible."""
    import subprocess
    import shutil
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shutil.copy(os.path.join(repo_root, "local_runner.py"), str(tmp_path / "local_runner.py"))
    (tmp_path / ".env").write_text("LOCAL_RUNNER_EARLY=false\n")
    script = (
        "import local_runner\n"
        "print('EARLY_ENABLED=' + str(local_runner.early_enabled()))\n"
    )
    (tmp_path / "check.py").write_text(script)
    clean_env = {k: v for k, v in os.environ.items() if k != "LOCAL_RUNNER_EARLY"}
    result = subprocess.run(["python3", "check.py"], cwd=str(tmp_path),
                            capture_output=True, text=True, timeout=30, env=clean_env)
    assert "EARLY_ENABLED=False" in result.stdout, (
        f"local_runner.py did not pick up LOCAL_RUNNER_EARLY=false from .env -- "
        f"stdout={result.stdout!r} stderr={result.stderr!r}")


def test_local_runner_defaults_early_enabled_true_without_env_override(tmp_path):
    pytest.importorskip("dotenv", reason="python-dotenv not installed in this test environment")
    """Contrast: with no .env override at all, early_enabled() must still default to True (the
    documented default) -- the dotenv fix must not flip the default, only make real overrides work.

    IMPORTANT: this subprocess must get a CLEAN environment, not inherit the pytest process's own
    os.environ -- see tests/conftest.py's own module docstring: importing dashboard.py anywhere in
    this pytest session loads Ali's REAL repo .env (including today's real LOCAL_RUNNER_EARLY=false)
    into the pytest process's environment, which subprocess.run() would otherwise silently pass
    through to the child, making this test fail for a completely different reason than a real bug
    (caught by this exact failure while writing this test)."""
    import subprocess
    import shutil
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shutil.copy(os.path.join(repo_root, "local_runner.py"), str(tmp_path / "local_runner.py"))
    # no .env file at all in this tmp_path
    script = (
        "import local_runner\n"
        "print('EARLY_ENABLED=' + str(local_runner.early_enabled()))\n"
    )
    (tmp_path / "check.py").write_text(script)
    clean_env = {k: v for k, v in os.environ.items() if k != "LOCAL_RUNNER_EARLY"}
    result = subprocess.run(["python3", "check.py"], cwd=str(tmp_path),
                            capture_output=True, text=True, timeout=30, env=clean_env)
    assert "EARLY_ENABLED=True" in result.stdout, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}")
