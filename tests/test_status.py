"""Check status against stale boot records and damaged external hooks."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from zte_f50_singbox.autostart import hook_block


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.base = Path(self.work.name)
        self.run = self.base / "run"
        self.run.mkdir()
        for name, value in {"deadline": "0", "owned": "old-session", "boot.attempt": "boot-previous",
                            "boot.result": "started"}.items():
            (self.run / name).write_text(value + "\n")
        self.hook = self.base / "user-boot.sh"
        self.hook.write_text("#!/bin/sh\n" + hook_block(str(self.base)))
        self.fallback = self.base / "user-schedule.sh"
        self.fallback.write_bytes(self.hook.read_bytes())
        source = (ROOT / "f50-proxy").read_text().split("command=${1:-help}")[0]
        source = source.replace("PATH=/system/bin:/system/xbin:/system/sbin", 'PATH="$TEST_HOST_PATH"', 1)
        source = source.replace("/system/bin/sh -n", "/bin/sh -n")
        source = source.replace("cat /proc/sys/kernel/random/boot_id", "printf boot-current")
        source += r'''
core_alive() { CPID=42; [ "$TEST_CORE" = running ]; }
guard_alive() { [ "$TEST_GUARD" = running ]; }
ipt() { return 0; }
ip() { return 0; }
if [ "$TEST_REAL_METADATA" != 1 ]; then
    autostart_metadata() {
        AUTOSTART_BACKEND=external-script
        AUTOSTART_HOOK=$TEST_HOOK
        AUTOSTART_FALLBACK_HOOK=$TEST_FALLBACK
    }
fi
show_status
'''
        self.controller = self.base / "f50-proxy"
        self.controller.write_text(source)

    def status(self, *, core="running", guard="running", real_metadata=False):
        env = dict(os.environ, TEST_HOST_PATH=os.environ["PATH"], TEST_CORE=core, TEST_GUARD=guard,
                   TEST_HOOK=str(self.hook), TEST_FALLBACK=str(self.fallback),
                   TEST_REAL_METADATA="1" if real_metadata else "0")
        result = subprocess.run(["/bin/sh", str(self.controller)], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_stale_confirmed_state_does_not_claim_active_watchdog_or_rules(self):
        for core, guard in (("stopped", "stopped"), ("running", "stopped"), ("stopped", "running")):
            with self.subTest(core=core, guard=guard):
                status = self.status(core=core, guard=guard)
                self.assertIn("lease: stale", status)
                self.assertIn("gateway: inactive (stale ownership record)", status)
                self.assertNotIn("watchdog active", status)
                self.assertIn("last boot: not attempted in this boot", status)

    def test_live_confirmed_session_and_current_boot_are_reported(self):
        (self.run / "boot.attempt").write_text("boot-current\n")
        status = self.status()
        self.assertIn("lease: confirmed (watchdog active)", status)
        self.assertIn("watchdog: running", status)
        self.assertIn("gateway: session owns rules", status)
        self.assertIn("boot hook health: healthy", status)
        self.assertIn(f"fallback hook: {self.fallback} (healthy)", status)
        self.assertIn("last boot: started", status)

    def test_nul_syntax_and_missing_entries_are_visible_without_execution(self):
        cases = [(b"\0" * 1825 + b": tail\n", "corrupt-NUL-bytes"),
                 (b"#!/bin/sh\nif then\n", "invalid-shell"),
                 (b"#!/bin/sh\n: no-project-entry\n", "missing-or-invalid-entry")]
        for content, health in cases:
            with self.subTest(health=health):
                self.hook.write_bytes(content)
                status = self.status()
                self.assertIn("boot hook health: " + health, status)
                self.assertIn(f"fallback hook: {self.fallback} (healthy)", status)
        self.hook.unlink()
        self.assertIn("boot hook health: missing-or-unsafe", self.status())

    def test_metadata_parser_accepts_declared_fallback_without_sourcing_it(self):
        (self.base / "autostart.conf").write_text(
            "AUTOSTART_BACKEND=external-script\nAUTOSTART_HOOK=/sdcard/primary.sh\n"
            "AUTOSTART_FALLBACK_HOOK=/sdcard/fallback.sh\n")
        status = self.status(real_metadata=True)
        self.assertIn("boot hook: /sdcard/primary.sh", status)
        self.assertIn("fallback hook: /sdcard/fallback.sh (missing-or-unsafe)", status)


if __name__ == "__main__":
    unittest.main()
