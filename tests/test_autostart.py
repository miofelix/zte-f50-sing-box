"""Exercise the real hook-registration shell against isolated local files."""

from argparse import Namespace
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zte_f50_singbox.autostart import (
    BEGIN_MARKER, END_MARKER, autostart, hook_block, hook_install_script,
    hook_preflight_script, render_autostart_config, validate_autostart,
)
from zte_f50_singbox.errors import SetupError
from zte_f50_singbox.profiles import load_profile


class HookRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.base = self.root / "gateway"
        self.base.mkdir()
        self.hook = self.root / "existing-boot.sh"
        self.fallback = self.root / "existing-schedule.sh"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        setsid = self.bin / "setsid"
        setsid.write_text("#!/bin/sh\nexit 0\n")
        setsid.chmod(0o700)
        sync = self.bin / "sync"
        sync.write_text('#!/bin/sh\nprintf "sync\\n" >> "$TEST_SYNC_LOG"\n')
        sync.chmod(0o700)
        self.profile = {
            "remote_root": str(self.base),
            "autostart": {"backend": "external-script", "hook_path": "/sdcard/test-boot.sh"},
        }

    def run_script(self, *, preflight=False):
        generator = hook_preflight_script if preflight else hook_install_script
        fragment = generator(self.profile)
        # Only adapt the Android syntax checker and profile's hook path. All
        # registration, marker checks, backups and writes execute unchanged.
        fragment = fragment.replace("/system/bin/sh -n", "/bin/sh -n")
        fragment = fragment.replace("hook=/sdcard/test-boot.sh", "hook=" + shlex.quote(str(self.hook)))
        fragment = fragment.replace("hook=/sdcard/test-schedule.sh", "hook=" + shlex.quote(str(self.fallback)))
        script = "set -eu\numask 077\nbase=" + shlex.quote(str(self.base)) + "\n" + fragment
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                   TEST_SYNC_LOG=str(self.root / "sync.log"))
        return subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, env=env)

    def test_install_preserves_existing_content_inode_permissions_and_private_backup(self):
        sentinel = self.root / "must-not-execute"
        original = ("#!/bin/sh\n# 已有启动任务\nprintf old >/dev/null\n"
                    f"touch {shlex.quote(str(sentinel))}").encode()
        self.hook.write_bytes(original)
        self.hook.chmod(0o751)
        before = self.hook.stat()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = original + b"\n" + hook_block(str(self.base)).encode()
        self.assertEqual(self.hook.read_bytes(), expected)
        after = self.hook.stat()
        self.assertEqual((before.st_ino, before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)),
                         (after.st_ino, after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)))
        backups = list((self.base / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.assertFalse(sentinel.exists(), "installer must never execute an existing hook")
        self.assertFalse((self.base / "autostart.disabled").exists(), "fresh install defaults to enabled")
        self.assertEqual((self.root / "sync.log").read_text().splitlines(), ["sync", "sync"])

    def test_nul_bytes_are_rejected_before_backup_or_write(self):
        for content in (b"\0" * 1825 + b": intact-tail\n", b"#!/bin/sh\n# a\0b\n", b"\0" * 200):
            with self.subTest(content=content[:20]):
                self.hook.write_bytes(content)
                for preflight in (True, False):
                    result = self.run_script(preflight=preflight)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("NUL bytes", result.stderr)
                    self.assertEqual(self.hook.read_bytes(), content)
                self.assertFalse((self.base / "backups").exists())

    def test_sync_failure_before_write_leaves_hook_untouched(self):
        original = b"#!/bin/sh\n: original\n"
        self.hook.write_bytes(original)
        (self.bin / "sync").write_text("#!/bin/sh\nexit 1\n")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.hook.read_bytes(), original)
        self.assertEqual(next((self.base / "backups").iterdir()).read_bytes(), original)

    def test_corruption_during_capture_is_not_installed_or_kept_as_backup(self):
        original = b"#!/bin/sh\n: original\n"
        self.hook.write_bytes(original)
        cat = self.bin / "cat"
        cat.write_text('#!/bin/sh\ncase "$1" in\n'
                       '  */existing-boot.sh) printf "\\000";;\n'
                       'esac\nexec /bin/cat "$@"\n')
        cat.chmod(0o700)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Captured boot hook contains NUL bytes", result.stderr)
        self.assertEqual(self.hook.read_bytes(), original)
        self.assertFalse((self.base / "backups").exists())

    def test_sync_failure_after_write_restores_original(self):
        original = b"#!/bin/sh\n: original\n"
        self.hook.write_bytes(original)
        (self.bin / "sync").write_text(
            '#!/bin/sh\nprintf "sync\\n" >> "$TEST_SYNC_LOG"\n'
            'test "$(wc -l < "$TEST_SYNC_LOG")" -ne 2\n')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.hook.read_bytes(), original)
        self.assertEqual(len((self.root / "sync.log").read_text().splitlines()), 3)

    def test_primary_and_fallback_are_registered_and_remain_idempotent(self):
        self.profile["autostart"]["fallback_hook_path"] = "/sdcard/test-schedule.sh"
        self.hook.write_text("#!/bin/sh\n: primary-task\n")
        self.fallback.write_text("#!/bin/sh\n: schedule-task\n")
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        originals = [path.read_bytes() for path in (self.hook, self.fallback)]
        backups = list((self.base / "backups").iterdir())
        self.assertEqual(len(backups), 2)
        for content in originals:
            self.assertEqual(content.count(BEGIN_MARKER.encode()), 1)
        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual([path.read_bytes() for path in (self.hook, self.fallback)], originals)
        self.assertEqual(list((self.base / "backups").iterdir()), backups)

    def test_invalid_fallback_is_rejected_before_changing_primary(self):
        self.profile["autostart"]["fallback_hook_path"] = "/sdcard/test-schedule.sh"
        original = b"#!/bin/sh\n: primary-task\n"
        self.hook.write_bytes(original)
        self.fallback.write_bytes(b"\0" * 200)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.hook.read_bytes(), original)
        self.assertFalse((self.base / "backups").exists())

    def test_repeated_hook_preserves_handled_boot_and_disabled_preference(self):
        entry = self.base / "boot-start.sh"
        entry.touch()
        run = self.base / "run"
        run.mkdir()
        calls = self.root / "hook-calls"
        (self.bin / "setsid").write_text('#!/bin/sh\nprintf "start\\n" >> "$TEST_HOOK_CALLS"\n')
        script = hook_block(str(self.base)).replace("cat /proc/sys/kernel/random/boot_id", "printf boot-current")
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"], TEST_HOOK_CALLS=str(calls))
        def trigger():
            result = subprocess.run(["/bin/sh", "-c", script + "\nwait\n"], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
        trigger()
        self.assertEqual(calls.read_text(), "start\n")
        (run / "boot.attempt").write_text("boot-current\n")
        trigger()
        self.assertEqual(calls.read_text(), "start\n", "a fallback must not restart a manually stopped session")
        (run / "boot.attempt").write_text("boot-previous\n")
        (self.base / "autostart.disabled").touch()
        trigger()
        self.assertEqual(calls.read_text(), "start\n")

    def test_registration_is_idempotent_and_preserves_explicit_off(self):
        self.hook.write_text("#!/bin/sh\n# leave me here\n")
        disabled = self.base / "autostart.disabled"
        disabled.write_bytes(b"user preference\n")
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        content = self.hook.read_bytes()
        backups = list((self.base / "backups").iterdir())
        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.hook.read_bytes(), content)
        self.assertEqual(list((self.base / "backups").iterdir()), backups)
        self.assertEqual(disabled.read_bytes(), b"user preference\n")
        self.assertEqual(content.count(BEGIN_MARKER.encode()), 1)

    def test_replace_block_preserves_prefix_and_suffix_bytes(self):
        prefix = b"#!/bin/sh\n# prefix\n"
        suffix = "# 后缀没有换行".encode()
        self.hook.write_bytes(prefix + (BEGIN_MARKER + "\n: old\n" + END_MARKER + "\n").encode() + suffix)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.hook.read_bytes(), prefix + hook_block(str(self.base)).encode() + suffix)

    def test_failed_write_restores_original_hook_from_private_backup(self):
        original = b"#!/bin/sh\n# important existing loader commands\n: keep\n"
        self.hook.write_bytes(original)
        failing_cat = self.bin / "cat"
        failing_cat.write_text("""#!/bin/sh
case "$1" in
  */.boot-hook.*) /bin/cat "$@" | head -c 8; exit 1;;
esac
exec /bin/cat "$@"
""")
        failing_cat.chmod(0o700)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restoring its backup", result.stderr)
        self.assertEqual(self.hook.read_bytes(), original)
        self.assertEqual(len(list((self.base / "backups").iterdir())), 1)

    def test_malformed_markers_are_rejected_without_writes(self):
        cases = [
            BEGIN_MARKER + "\n: partial\n",
            END_MARKER + "\n" + BEGIN_MARKER + "\n",
            BEGIN_MARKER + "\n" + BEGIN_MARKER + "\n" + END_MARKER + "\n",
            "# prefix " + BEGIN_MARKER + "\n" + END_MARKER + "\n",
            BEGIN_MARKER + "\r\n" + END_MARKER + "\n",
        ]
        for content in cases:
            with self.subTest(content=content):
                original = ("#!/bin/sh\n" + content).encode()
                self.hook.write_bytes(original)
                for preflight in (True, False):
                    result = self.run_script(preflight=preflight)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.hook.read_bytes(), original)
                self.assertFalse((self.base / "backups").exists())

    def test_missing_symlink_oversized_and_invalid_shell_hooks_are_rejected(self):
        for kind in ("missing", "symlink", "oversized", "syntax"):
            with self.subTest(kind=kind):
                if self.hook.is_symlink() or self.hook.exists():
                    self.hook.unlink()
                if kind == "symlink":
                    target = self.root / "target"
                    target.write_text("#!/bin/sh\n")
                    self.hook.symlink_to(target)
                elif kind == "oversized":
                    self.hook.write_bytes(b"#" * 262145)
                elif kind == "syntax":
                    self.hook.write_text("#!/bin/sh\nif then\n")
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.base / "backups").exists())


class AutostartProfileAndCliTests(unittest.TestCase):
    def test_default_profile_selects_existing_hook_backend(self):
        profile = load_profile("zte-f50")
        self.assertEqual(profile["autostart"], {
            "backend": "external-script", "hook_path": "/sdcard/ufi_tools_boot.sh",
            "fallback_hook_path": "/sdcard/ufi_tools_schedule.sh",
        })
        self.assertEqual(render_autostart_config(profile),
                         "AUTOSTART_BACKEND=external-script\nAUTOSTART_HOOK=/sdcard/ufi_tools_boot.sh\n"
                         "AUTOSTART_FALLBACK_HOOK=/sdcard/ufi_tools_schedule.sh\n")
        self.assertEqual(render_autostart_config({}), "AUTOSTART_BACKEND=none\nAUTOSTART_HOOK=none\n")

    def test_adapter_rejects_protected_unsafe_and_parent_paths(self):
        for path in ("/system/etc/init.rc", "/vendor/a.sh", "relative.sh", "/sdcard/../data/a.sh",
                     "/data/./boot.sh", "/sdcard/a\n.sh", "/data/$(id).sh", "/sdcard/boot;reboot"):
            with self.subTest(path=path), self.assertRaises(SetupError):
                validate_autostart({"backend": "external-script", "hook_path": path})
        for value in ([], {}, {"backend": "init", "hook_path": "/data/boot.sh"}):
            with self.subTest(value=value), self.assertRaises(SetupError):
                validate_autostart(value)
        for fallback in ("/system/fallback.sh", "/data/../boot.sh", "/data/boot.sh", None):
            with self.subTest(fallback=fallback), self.assertRaises(SetupError):
                validate_autostart({"backend": "external-script", "hook_path": "/data/boot.sh",
                                    "fallback_hook_path": fallback})

    @patch("zte_f50_singbox.autostart.ssh")
    def test_enable_repairs_hook_in_one_operation_and_invokes_device_toggle(self, remote):
        autostart(Namespace(profile="zte-f50", host="other-f50", action="on"))
        remote.assert_called_once()
        host, script = remote.call_args.args
        self.assertEqual(host, "other-f50")
        self.assertIn("flock -n 9", script)
        self.assertIn("Registered boot hook", script)
        self.assertTrue(script.endswith('"$base/f50-proxy" autostart on\n'))
        self.assertIn(')\n"$base/f50-proxy"', script, "release registration lock before controller takes it")
        self.assertIn('mv "$metadata" "$base/autostart.conf"', script)

    @patch("zte_f50_singbox.autostart.ssh")
    def test_disable_does_not_rewrite_existing_hook_or_stop_proxy(self, remote):
        autostart(Namespace(profile="zte-f50", host=None, action="off"))
        script = remote.call_args.args[1]
        self.assertIn('"$base/f50-proxy" autostart off', script)
        self.assertNotIn("Registered boot hook", script)
        self.assertNotIn(" stop", script)


if __name__ == "__main__":
    unittest.main()
