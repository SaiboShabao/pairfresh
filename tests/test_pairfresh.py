import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from pairfresh import check_pair, parse_timestamp

ROOT = Path(__file__).resolve().parents[1]
NOW = parse_timestamp("2026-10-21T04:00:10Z")


def healthy_pair():
    return {
        "pair": "DEMO/USDT", "research_cycle": "cycle-002",
        "legs": [
            {"name": "spot", "research_cycle": "cycle-002", "fetch_ok": True,
             "timestamp": "2026-10-21T04:00:08Z"},
            {"name": "perp", "research_cycle": "cycle-002", "fetch_ok": True,
             "timestamp": "2026-10-21T04:00:08.500Z"},
        ],
    }


class PairChecks(unittest.TestCase):
    def test_healthy_pair_and_no_input_mutation(self):
        record = healthy_pair()
        before = copy.deepcopy(record)
        result = check_pair(record, as_of=NOW)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["skew_ms"], 500)
        self.assertEqual(result["legs"][0]["age_ms"], 2000)
        self.assertEqual(record, before)

    def test_recent_cached_leg_from_previous_cycle_is_rejected(self):
        record = healthy_pair()
        record["legs"][0]["research_cycle"] = "cycle-001"
        result = check_pair(record, as_of=NOW)
        self.assertFalse(result["accepted"])
        self.assertIn("legs[0]:cycle_mismatch", result["reasons"])

    def test_failed_or_missing_fetch_status_is_rejected(self):
        for status in (False, None, 1, "true"):
            with self.subTest(status=status):
                record = healthy_pair()
                record["legs"][1]["fetch_ok"] = status
                result = check_pair(record, as_of=NOW)
                self.assertFalse(result["accepted"])
                self.assertIn("legs[1]:fetch_not_successful", result["reasons"])

    def test_one_stale_leg_and_strict_ttl_boundary(self):
        for timestamp in ("2026-10-21T04:00:04Z", "2026-10-21T04:00:05Z"):
            with self.subTest(timestamp=timestamp):
                record = healthy_pair()
                record["legs"][0]["timestamp"] = timestamp
                result = check_pair(record, as_of=NOW, max_skew_ms=10000)
                self.assertIn("legs[0]:stale", result["reasons"])
                self.assertFalse(result["accepted"])

    def test_fresh_legs_can_still_be_too_far_apart(self):
        record = healthy_pair()
        record["legs"][0]["timestamp"] = "2026-10-21T04:00:06Z"
        result = check_pair(record, as_of=NOW)
        self.assertEqual(result["reasons"], ["legs:skew_exceeded"])

    def test_skew_boundary_is_inclusive(self):
        record = healthy_pair()
        record["legs"][0]["timestamp"] = "2026-10-21T04:00:07.500Z"
        self.assertTrue(check_pair(record, as_of=NOW)["accepted"])

    def test_future_timestamp_is_rejected(self):
        record = healthy_pair()
        record["legs"][0]["timestamp"] = "2026-10-21T04:00:10.001Z"
        result = check_pair(record, as_of=NOW, max_skew_ms=10000)
        self.assertIn("legs[0]:future_timestamp", result["reasons"])

    def test_timezone_offsets_are_normalized(self):
        record = healthy_pair()
        record["legs"][0]["timestamp"] = "2026-10-21T12:00:08+08:00"
        self.assertTrue(check_pair(record, as_of=NOW)["accepted"])

    def test_invalid_and_ambiguous_timestamps_are_rejected(self):
        for timestamp in (None, 123, "nonsense", "2026-10-21T04:00:08"):
            with self.subTest(timestamp=timestamp):
                record = healthy_pair()
                record["legs"][0]["timestamp"] = timestamp
                result = check_pair(record, as_of=NOW)
                self.assertIn("legs[0]:invalid_timestamp", result["reasons"])

    def test_malformed_records_fail_closed(self):
        for record in (None, [], {}, {"pair": "x", "research_cycle": "c", "legs": [None, {}]}):
            with self.subTest(record=record):
                self.assertFalse(check_pair(record, as_of=NOW)["accepted"])

    def test_duplicate_leg_names_and_missing_leg(self):
        record = healthy_pair()
        record["legs"][1]["name"] = "spot"
        self.assertIn("legs[1]:duplicate_name", check_pair(record, as_of=NOW)["reasons"])
        record["legs"].pop()
        self.assertIn("legs:expected_exactly_two", check_pair(record, as_of=NOW)["reasons"])

    def test_cycle_ids_are_not_coerced(self):
        record = healthy_pair()
        record["research_cycle"] = 2
        record["legs"][0]["research_cycle"] = "2"
        record["legs"][1]["research_cycle"] = 2
        self.assertIn("legs[0]:cycle_mismatch", check_pair(record, as_of=NOW)["reasons"])
        record["research_cycle"] = True
        self.assertIn("research_cycle:missing_or_invalid", check_pair(record, as_of=NOW)["reasons"])

    def test_invalid_configuration_is_an_error(self):
        for config in ({"max_age_ms": 0}, {"max_age_ms": True}, {"max_skew_ms": -1},
                       {"as_of": "2026-10-21"}):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    check_pair(healthy_pair(), **config)


class CommandLineChecks(unittest.TestCase):
    def run_cli(self, *args, data=None):
        return subprocess.run([sys.executable, str(ROOT / "pairfresh.py"), *args],
                              input=data, capture_output=True, text=True, check=False)

    def test_replay_example_has_one_accepted_and_five_rejected(self):
        run = self.run_cli(str(ROOT / "examples" / "demo.jsonl"), "--as-of", NOW.isoformat())
        self.assertEqual(run.returncode, 1, run.stderr)
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual([row["accepted"] for row in rows], [True, False, False, False, False, False])
        self.assertEqual(json.loads(run.stderr),
                         {"mode": "replay", "total": 6, "accepted": 1, "rejected": 5})

    def test_live_mode_checks_current_clock_and_rejects_old_snapshots(self):
        record = healthy_pair()
        for leg in record["legs"]:
            leg["timestamp"] = "2000-01-01T00:00:00Z"
        run = self.run_cli("-", data=json.dumps(record))
        self.assertEqual(run.returncode, 1, run.stderr)
        row = json.loads(run.stdout)
        self.assertEqual(row["mode"], "live")
        self.assertIn("legs[0]:stale", row["reasons"])

    def test_invalid_json_does_not_hide_following_records(self):
        run = self.run_cli("-", "--as-of", NOW.isoformat(),
                           data="broken\n\n" + json.dumps(healthy_pair()) + "\n")
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertEqual(rows[0]["reasons"], ["record:invalid_json"])
        self.assertEqual(rows[1]["line"], 3)
        self.assertTrue(rows[1]["accepted"])

    def test_all_healthy_records_exit_zero(self):
        run = self.run_cli("-", "--as-of", NOW.isoformat(), data=json.dumps(healthy_pair()))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stderr)["accepted"], 1)

    def test_empty_input_does_not_pass(self):
        run = self.run_cli("-", data="\n  \n")
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stderr)["error"], "no_records")

    def test_invalid_arguments_and_missing_file_exit_two(self):
        for args in (("-", "--as-of", "2026-10-21"), ("-", "--max-age-ms", "0"),
                     ("-", "--max-skew-ms", "-1"), (str(ROOT / "does-not-exist.jsonl"),)):
            with self.subTest(args=args):
                run = self.run_cli(*args, data="")
                self.assertEqual(run.returncode, 2)

    def test_nonstandard_json_constants_are_rejected(self):
        run = self.run_cli("-", data='{"pair": NaN}')
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["reasons"], ["record:invalid_json"])

    def test_duplicate_json_fields_are_rejected(self):
        data = json.dumps(healthy_pair()).replace('"fetch_ok": true',
                                                 '"fetch_ok": false, "fetch_ok": true', 1)
        run = self.run_cli("-", "--as-of", NOW.isoformat(), data=data)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["reasons"], ["record:invalid_json"])

    def test_deep_nonobject_input_is_rejected_and_processing_continues(self):
        data = "[" * 2000 + "0" + "]" * 2000 + "\n" + json.dumps(healthy_pair())
        run = self.run_cli("-", "--as-of", NOW.isoformat(), data=data)
        self.assertEqual(run.returncode, 1, run.stderr)
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertFalse(rows[0]["accepted"])
        self.assertTrue(rows[0]["reasons"])
        self.assertTrue(rows[1]["accepted"])


if __name__ == "__main__":
    unittest.main()
