"""Run the production boot state machine with a finite clock and private files.

No device, network tools, elevated privileges, or real sleeps are used. The
single-process harness replaces flock; hook registration has separate tests.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ANDROID_PATH = "PATH=/system/bin:/system/xbin:/system/sbin"


class BootRunnerTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.base = Path(self.work.name)
        self.run = self.base / "run"
        self.run.mkdir()
        (self.base / "clock").write_text("0\n")
        source = (ROOT / "boot-start.sh").read_text()
        marker = "# === BOOT DISPATCH ==="
        self.assertEqual(source.count(marker), 1)
        functions = source.split(marker)[0]
        self.assertIn(ANDROID_PATH, functions)
        # Keep all production functions intact; Android's executable search
        # path is the only platform-specific initialization adapted here.
        functions = functions.replace(ANDROID_PATH, 'PATH="$TEST_HOST_PATH"', 1)
        (self.base / "boot-functions.sh").write_text(functions)
        self.runner = self.base / "run-test.sh"
        self.runner.write_text(
            '. "' + str(self.base / "boot-functions.sh") + '"\n' + r'''
id() { printf '0\n'; }
flock() { return 0; }
boot_identity() { printf 'fake-boot-1\n'; }
boot_uptime() { cat "$BOOT_BASE/clock"; }
boot_sleep() {
    fake_now=$(cat "$BOOT_BASE/clock")
    printf '%s\n' "$((fake_now + $1))" > "$BOOT_BASE/clock"
}
fake_tick() {
    fake_count=0
    [ ! -f "$BOOT_BASE/$1.count" ] || fake_count=$(cat "$BOOT_BASE/$1.count")
    printf '%s\n' "$((fake_count + 1))" > "$BOOT_BASE/$1.count"
}
boot_ready() {
    fake_tick ready
    case "$READY_MODE" in
        ready) return 0;;
        timeout) return 1;;
        disable) : > "$BOOT_BASE/autostart.disabled"; return 1;;
        manual)
            printf 'manual-session\n' > "$BOOT_RUN/session"
            : > "$BOOT_RUN/alive"
            return 0;;
    esac
    return 1
}
boot_probe() {
    fake_tick probe
    printf 'probe\n' >> "$BOOT_BASE/calls"
    case "$PROBE_MODE" in
        healthy) return 0;;
        fail) return 1;;
        disable) : > "$BOOT_BASE/autostart.disabled"; return 0;;
        replace)
            printf 'manual-session\n' > "$BOOT_RUN/session"
            return 1;;
    esac
    return 1
}
boot_control() {
    printf '%s\n' "$*" >> "$BOOT_BASE/calls"
    case "$1" in
        _alive) [ -e "$BOOT_RUN/alive" ];;
        _boot-config) printf 'LAN_INTERFACE=br0\nLOCAL_TEST_PORT=7890\n';;
        _boot-start)
            [ ! -e "$BOOT_RUN/alive" ] || return 3
            [ ! -e "$BOOT_BASE/autostart.disabled" ] || return 4
            printf 'start %s\n' "$2" >> "$BOOT_BASE/calls"
            [ "$START_MODE" != fail ] || return 1
            printf 'boot-session\n' > "$BOOT_RUN/session"
            : > "$BOOT_RUN/alive"
            printf 'BOOT_SESSION=boot-session\n';;
        _boot-confirm)
            [ "$(cat "$BOOT_RUN/session")" = "$2" ] &&
                [ ! -e "$BOOT_BASE/autostart.disabled" ] || return 1
            printf '%s\n' "$2" > "$BOOT_RUN/confirmed";;
        _boot-stop)
            [ "$(cat "$BOOT_RUN/session")" = "$2" ] || return 0
            rm -f "$BOOT_RUN/session" "$BOOT_RUN/alive";;
        *) return 99;;
    esac
}
boot_main
'''
        )

    def run_boot(self, *, ready="ready", probe="healthy", start="ok"):
        env = dict(os.environ, TEST_HOST_PATH=os.environ["PATH"],
                   READY_MODE=ready, PROBE_MODE=probe, START_MODE=start)
        return subprocess.run(["/bin/sh", str(self.runner)], env=env,
                              capture_output=True, text=True, timeout=5)

    def calls(self):
        path = self.base / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def result(self):
        return (self.run / "boot.result").read_text().strip()

    def test_default_on_starts_with_bounded_lease_then_checks_and_confirms(self):
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result(), "started")
        self.assertFalse((self.base / "autostart.disabled").exists())
        self.assertEqual((self.run / "confirmed").read_text(), "boot-session\n")
        self.assertEqual((self.run / "boot.attempt").read_text(), "fake-boot-1\n")
        calls = self.calls()
        self.assertEqual(calls.count("_boot-start 120"), 1)
        self.assertEqual(calls.count("start 120"), 1)
        self.assertLess(calls.index("start 120"), calls.index("probe"))
        self.assertLess(calls.index("probe"), calls.index("_boot-confirm boot-session"))
        self.assertFalse(any(call.startswith("_boot-stop") for call in calls))

    def test_explicit_off_never_starts_or_records_an_attempt(self):
        (self.base / "autostart.disabled").touch()
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.result(), "disabled")
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.run / "boot.attempt").exists())

    def test_one_attempt_per_boot_even_when_previous_health_check_failed(self):
        first = self.run_boot(probe="fail")
        self.assertNotEqual(first.returncode, 0)
        before = self.calls()
        second = self.run_boot()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.calls(), before)
        self.assertEqual(self.result(), "health-failed")
        self.assertFalse((self.run / "confirmed").exists())

    def test_existing_manual_session_is_preserved_before_or_during_readiness(self):
        for during_wait in (False, True):
            with self.subTest(during_wait=during_wait):
                for path in self.run.iterdir():
                    path.unlink()
                (self.base / "calls").unlink(missing_ok=True)
                if not during_wait:
                    (self.run / "session").write_text("manual-session\n")
                    (self.run / "alive").touch()
                result = self.run_boot(ready="manual" if during_wait else "ready")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.result(), "already-running")
                self.assertEqual((self.run / "session").read_text(), "manual-session\n")
                self.assertFalse((self.run / "confirmed").exists())
                self.assertFalse(any(call == "stop" or call.startswith(
                    ("start ", "_boot-confirm", "_boot-stop")) for call in self.calls()))
                if during_wait:
                    # The manual session appears after the initial _alive
                    # optimization, so only the locked start guard protects it.
                    self.assertEqual(self.calls().count("_alive"), 1)
                    self.assertIn("_boot-start 120", self.calls())

    def test_failed_health_checks_stop_owned_session_without_confirmation(self):
        result = self.run_boot(probe="fail")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.result(), "health-failed")
        self.assertEqual(self.calls().count("probe"), 3)
        self.assertEqual(self.calls().count("_boot-stop boot-session"), 1)
        self.assertFalse((self.run / "session").exists())
        self.assertFalse((self.run / "confirmed").exists())

    def test_start_failure_does_not_probe_or_confirm(self):
        result = self.run_boot(start="fail")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.result(), "start-failed")
        self.assertIn("start 120", self.calls())
        self.assertNotIn("probe", self.calls())
        self.assertFalse((self.run / "session").exists())
        self.assertFalse((self.run / "confirmed").exists())

    def test_readiness_timeout_never_enables_interception(self):
        result = self.run_boot(ready="timeout")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.result(), "network-timeout")
        self.assertEqual(int((self.base / "clock").read_text()), 180)
        self.assertFalse(any(call == "stop" or call.startswith("start ") for call in self.calls()))
        self.assertFalse((self.run / "session").exists())

    def test_switching_off_during_wait_or_probe_prevents_confirmation(self):
        for stage in ("readiness", "probe"):
            with self.subTest(stage=stage):
                for path in self.run.iterdir():
                    path.unlink()
                (self.base / "calls").unlink(missing_ok=True)
                (self.base / "autostart.disabled").unlink(missing_ok=True)
                result = self.run_boot(ready="disable" if stage == "readiness" else "ready",
                                       probe="disable" if stage == "probe" else "healthy")
                self.assertEqual(self.result(), "disabled", result.stderr)
                self.assertFalse((self.run / "confirmed").exists())
                self.assertFalse((self.run / "session").exists())
                if stage == "readiness":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("start 120", self.calls())
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("_boot-stop boot-session", self.calls())
                    self.assertFalse(any(call.startswith("_boot-confirm") for call in self.calls()))

    def test_cleanup_preserves_newer_session_created_during_probe(self):
        result = self.run_boot(probe="replace")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.result(), "health-failed")
        self.assertIn("_boot-stop boot-session", self.calls())
        self.assertEqual((self.run / "session").read_text(), "manual-session\n")
        self.assertFalse((self.run / "confirmed").exists())

    def test_real_controller_guards_generation_manual_session_and_disabled_state(self):
        source = (ROOT / "f50-proxy").read_text()
        marker = "command=${1:-help}"
        self.assertEqual(source.count(marker), 1)
        source = source.replace(ANDROID_PATH, 'PATH="$TEST_HOST_PATH"', 1)
        stubs = r'''
id() { printf '0\n'; }
flock() { return 0; }
confirm_session() { : > "$BASE/unexpected-confirm"; }
stop_all() { : > "$BASE/unexpected-stop"; }
start_all() { : > "$BASE/unexpected-start"; }
core_alive() { [ -e "$BASE/manual-core" ]; }
'''
        controller = self.base / "f50-proxy"
        controller.write_text(source.replace(marker, stubs + "\n" + marker, 1))
        (self.run / "session").write_text("newer-session\n")
        for command, expected in (("_boot-confirm", 1), ("_boot-stop", 0)):
            with self.subTest(command=command):
                result = subprocess.run(
                    ["/bin/sh", str(controller), command, "older-session"],
                    env=dict(os.environ, TEST_HOST_PATH=os.environ["PATH"]),
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual((self.run / "session").read_text(), "newer-session\n")
                self.assertFalse((self.base / "unexpected-confirm").exists())
                self.assertFalse((self.base / "unexpected-stop").exists())
        for condition, expected in (("manual-core", 3), ("autostart.disabled", 4)):
            with self.subTest(atomic_start_guard=condition):
                state = self.base / condition
                state.touch()
                result = subprocess.run(
                    ["/bin/sh", str(controller), "_boot-start", "120"],
                    env=dict(os.environ, TEST_HOST_PATH=os.environ["PATH"]),
                    capture_output=True, text=True, timeout=5,
                )
                state.unlink()
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual((self.run / "session").read_text(), "newer-session\n")
                self.assertFalse((self.base / "unexpected-stop").exists())
                self.assertFalse((self.base / "unexpected-start").exists())


if __name__ == "__main__":
    unittest.main()
