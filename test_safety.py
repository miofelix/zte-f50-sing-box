#!/usr/bin/env python3
"""Exercise controller transactions locally; no SSH, root, or real network changes.

The real shell functions run under /bin/sh with only ip/iptables replaced by a
small stateful model. This checks rollback and ownership, not kernel forwarding;
IPv4/IPv6 traffic and watchdog expiry still need device integration checks.
"""

import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
OWN_CHAINS = {"F50SB_PRE", "F50SB_IN", "F50SB_NAT"}
MARK = "0x40000000/0x40000000"


def initial_state():
    firewall = {}
    for family in ("iptables", "ip6tables"):
        firewall[family] = {
            "mangle": {
                "PREROUTING": [["-j", "ANDROID_MANGLE"]],
                "ANDROID_MANGLE": [["-j", "RETURN"]],
            },
            "filter": {
                "INPUT": [["-j", "ANDROID_INPUT"]],
                "ANDROID_INPUT": [["-i", "tailscale0", "-j", "ACCEPT"]],
            },
            "nat": {
                "PREROUTING": [["-j", "ADGUARD_REDIRECT_LAN"]],
                "ADGUARD_REDIRECT_LAN": [["-p", "udp", "--dport", "53", "-j", "RETURN"]],
            },
        }
    return {
        "firewall": firewall,
        "routes": {family: [["default", "dev", "tailscale0", "table", "52"]]
                   for family in ("-4", "-6")},
        "rules": {family: [["priority", "5270", "table", "52"]]
                  for family in ("-4", "-6")},
        "mutations": 0,
        "fail_at": 0,
        "events": [],
        "invariant_errors": [],
    }


def target(rule):
    return rule[rule.index("-j") + 1] if "-j" in rule else ""


def capture_published(state):
    return any(target(rule) == "F50SB_PRE"
               for tables in state["firewall"].values()
               for rule in tables["mangle"]["PREROUTING"])


def ready_to_publish(state):
    for family, prefix in (("iptables", "0.0.0.0/0"), ("ip6tables", "::/0")):
        tables = state["firewall"][family]
        if not tables["mangle"].get("F50SB_PRE") or not tables["filter"].get("F50SB_IN"):
            return False
        if not any(target(rule) == "F50SB_IN" for rule in tables["filter"]["INPUT"]):
            return False
        address_family = "-4" if family == "iptables" else "-6"
        if ["local", prefix, "dev", "lo", "table", "20250"] not in state["routes"][address_family]:
            return False
        expected = ["priority", "1000", "iif", "br0", "fwmark", MARK, "table", "20250"]
        if expected not in state["rules"][address_family]:
            return False
    return any(target(rule) == "F50SB_NAT"
               for rule in state["firewall"]["iptables"]["nat"]["PREROUTING"])


def mutate(state, command, action, callback):
    state["mutations"] += 1
    failed = state["mutations"] == state["fail_at"]
    state["events"].append({"phase": os.environ.get("MOCK_PHASE"),
                            "command": command, "failed": failed})
    if failed:
        return 1
    if capture_published(state) and action not in ("publish", "unpublish"):
        state["invariant_errors"].append("Changed prerequisites before removing capture hooks: " + repr(command))
        return 2
    if action == "publish" and not ready_to_publish(state):
        state["invariant_errors"].append("Published before both families were ready: " + repr(command))
        return 2
    return callback()


def mock_iptables(state, program, args):
    if args[:2] == ["-w", "5"]:
        args = args[2:]
    if len(args) < 4 or args[0] != "-t":
        raise ValueError("Unexpected iptables command: " + repr(args))
    table, operation, chain = args[1:4]
    rules = state["firewall"][program][table]
    rest = args[4:]
    if operation in ("-S", "-L"):
        return int(chain not in rules)
    if operation == "-C":
        return int(chain not in rules or rest not in rules[chain])
    is_hook = chain in ("PREROUTING", "INPUT") and target(rest[1:] if operation == "-I" else rest) in OWN_CHAINS
    if chain not in OWN_CHAINS and not is_hook:
        raise ValueError("Controller tried to mutate a foreign chain: " + repr(args))
    action = "change"
    if table == "mangle" and is_hook:
        action = "publish" if operation == "-I" else "unpublish"

    def apply():
        if operation == "-N":
            if chain in rules:
                return 1
            rules[chain] = []
        elif chain not in rules:
            return 1
        elif operation == "-A":
            rules[chain].append(rest)
        elif operation == "-I":
            rules[chain].insert(int(rest[0]) - 1, rest[1:])
        elif operation == "-D":
            if rest not in rules[chain]:
                return 1
            rules[chain].remove(rest)
        elif operation == "-F":
            rules[chain] = []
        elif operation == "-X":
            if rules[chain] or any(target(rule) == chain for entries in rules.values() for rule in entries):
                return 1
            del rules[chain]
        else:
            raise ValueError("Unexpected iptables operation: " + operation)
        return 0

    return mutate(state, [program, *args], action, apply)


def mock_ip(state, args):
    if len(args) >= 4 and args[1:4] == ["-o", "addr", "show"]:
        if args[0] == "-4":
            print("1: lo inet 127.0.0.1/8 scope host lo")
            print("13: sipa_eth0 inet 10.86.122.95/8 scope global sipa_eth0")
            print("34: br0 inet 192.168.0.1/24 scope global br0")
            print("38: tailscale0 inet 100.126.220.50/32 scope global tailscale0")
        else:
            print("1: lo inet6 ::1/128 scope host")
            print("13: sipa_eth0 inet6 2001:db8::10/64 scope global")
            print("34: br0 inet6 2001:db8::25/64 scope global")
            print("34: br0 inet6 fe80::25/64 scope link")
        return 0
    family, kind, operation = args[:3]
    entries = state["routes" if kind == "route" else "rules"][family]
    rest = args[3:]
    if operation == "show":
        if kind == "route":
            table = rest[rest.index("table") + 1]
            for entry in entries:
                if entry[entry.index("table") + 1] == table:
                    print(" ".join(entry))
        else:
            for entry in entries:
                print(entry[1] + ": from all lookup " + entry[-1])
        return 0
    if "table" not in rest or rest[rest.index("table") + 1] != "20250":
        raise ValueError("Controller tried to mutate a foreign route or rule: " + repr(args))

    def apply():
        if operation == "add":
            if rest in entries:
                return 1
            entries.append(rest)
        elif operation == "del":
            if rest not in entries:
                return 1
            entries.remove(rest)
        else:
            raise ValueError("Unexpected ip operation: " + operation)
        return 0

    return mutate(state, ["ip", *args], "change", apply)


def mock_main():
    path = Path(os.environ["MOCK_STATE"])
    state = json.loads(path.read_text())
    program = sys.argv[2]
    try:
        result = (mock_ip(state, sys.argv[3:]) if program == "ip"
                  else mock_iptables(state, program, sys.argv[3:]))
    except (ValueError, KeyError, IndexError) as error:
        state["invariant_errors"].append(str(error))
        result = 2
    path.write_text(json.dumps(state))
    return result


# Each modeled command is a short subprocess. Avoid loading the test runner and
# site packages for those commands, keeping exhaustive fault injection quick.
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--mock":
    sys.exit(mock_main())

import shlex
import subprocess
import tempfile
import unittest


class TransactionSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / "f50-proxy").read_text()
        marker = "\ncommand=${1:-help}\n"
        if source.count(marker) != 1:
            raise AssertionError("Cannot locate controller dispatch safely")
        # Android's fixed PATH does not exist on a development Mac. Preserve
        # host utilities until the temporary mock PATH is installed below.
        cls.functions = source.split(marker)[0].replace(
            "PATH=/system/bin:/system/xbin:/system/sbin\n", "")

    def run_controller(self, script, fail_at=0):
        with tempfile.TemporaryDirectory(prefix="f50-safety-") as folder:
            folder = Path(folder)
            mock_bin = folder / "bin"
            mock_bin.mkdir()
            (folder / "run").mkdir()
            state_file = folder / "state.json"
            state = initial_state()
            state["fail_at"] = fail_at
            state_file.write_text(json.dumps(state))
            for program in ("iptables", "ip6tables", "ip"):
                mock = mock_bin / program
                mock.write_text("#!/bin/sh\nexec " + shlex.join(
                    [sys.executable, "-S", str(Path(__file__).resolve()), "--mock", program]) + ' "$@"\n')
                mock.chmod(0o700)
            driver = folder / "driver.sh"
            driver.write_text(self.functions + "\n" +
                              'PATH="$TEST_MOCK_BIN:$TEST_SYSTEM_PATH"\nexport PATH\n' +
                              # No PID probing or termination is needed for these
                              # firewall transactions, even if a regression occurs.
                              'stop_core() { :; }\n' + script)
            environment = dict(os.environ, MOCK_STATE=str(state_file),
                               TEST_MOCK_BIN=str(mock_bin), TEST_SYSTEM_PATH=os.environ["PATH"])
            result = subprocess.run(["/bin/sh", str(driver)], env=environment,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads(state_file.read_text())
            self.assertFalse(state["invariant_errors"], state["invariant_errors"])
            return state, result.stdout

    def assert_restored(self, state):
        original = initial_state()
        for key in ("firewall", "routes", "rules"):
            self.assertEqual(state[key], original[key], key)

    def test_every_attach_failure_rolls_back_without_changing_foreign_state(self):
        scenario = """
export MOCK_PHASE=attach
if attach; then echo ATTACH_OK; else echo ATTACH_FAILED; fi
export MOCK_PHASE=cleanup
detach || exit 10
detach || exit 11
"""
        state, output = self.run_controller(scenario)
        self.assertIn("ATTACH_OK", output)
        self.assert_restored(state)
        mutations = len([event for event in state["events"] if event["phase"] == "attach"])
        self.assertGreater(mutations, 20, "Expected to cover both families and route activation")
        for position in range(1, mutations + 1):
            with self.subTest(failed_attach_mutation=position):
                state, output = self.run_controller(scenario, fail_at=position)
                self.assertIn("ATTACH_FAILED", output)
                self.assertEqual(len([event for event in state["events"] if event["failed"]]), 1)
                self.assert_restored(state)

    def test_failed_cleanup_keeps_ownership_and_can_be_retried(self):
        # Find the first cleanup mutation without coupling to the current number
        # of private-chain rules.
        state, _ = self.run_controller("""
export MOCK_PHASE=attach
attach || exit 12
export MOCK_PHASE=cleanup
detach || exit 13
""")
        first_cleanup = next(index + 1 for index, event in enumerate(state["events"])
                             if event["phase"] == "cleanup")
        state, output = self.run_controller("""
echo session > "$RUN/owned"
export MOCK_PHASE=attach
attach || exit 14
export MOCK_PHASE=cleanup
if stop_all; then exit 15; fi
[ -f "$RUN/owned" ] || exit 16
echo OWNERSHIP_RETAINED
export MOCK_PHASE=retry
stop_all || exit 17
[ ! -e "$RUN/owned" ] || exit 18
""", fail_at=first_cleanup)
        self.assertIn("OWNERSHIP_RETAINED", output)
        self.assert_restored(state)

    def test_watchdog_keeps_monitoring_after_successful_noop_expiry(self):
        # A queued expiry can successfully do nothing after confirm wins its
        # lock. Success must not make the watchdog abandon a live session.
        state, output = self.run_controller("""
export RUN
SELF="$BASE/fake-expire"
cat > "$SELF" <<'EXPIRE'
#!/bin/sh
[ "$1" = _expire ] && [ "$2" = session ] || exit 21
calls=0
[ ! -f "$RUN/expire.calls" ] || read -r calls < "$RUN/expire.calls"
calls=$((calls + 1))
echo "$calls" > "$RUN/expire.calls"
echo "EXPIRY_CALL=$calls"
case "$calls" in
    1) echo 0 > "$RUN/deadline";;
    2) rm -f "$RUN/session";;
    *) exit 22;;
esac
exit 0
EXPIRE
chmod 700 "$SELF"
core_alive() { return 1; }
sleep() { :; }
echo session > "$RUN/session"
echo 0 > "$RUN/deadline"
watchdog session
[ ! -e "$RUN/session" ] || exit 23
echo WATCHDOG_FINISHED
""")
        self.assertIn("EXPIRY_CALL=1", output)
        self.assertIn("EXPIRY_CALL=2", output)
        self.assertIn("WATCHDOG_FINISHED", output)
        self.assertEqual(output.count("EXPIRY_CALL="), 2)
        self.assert_restored(state)


if __name__ == "__main__":
    unittest.main()
