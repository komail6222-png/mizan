"""RASID on real load (OPSD): the data rules, the sizing and the fetch, on a generated stand-in file."""
import contextlib
import csv
import hashlib
import io
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mizan.core.contract import Refused
from tests import opsd_fixture as fx

HOUR = timedelta(hours=1)


def _reading(path, column, t):
    stamp = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["utc_timestamp"] == stamp:
                return float(row[column])
    return None


class DataRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mizan import opsd
        cls.tmp = tempfile.TemporaryDirectory()
        cls.csv = fx.make_opsd_csv(Path(cls.tmp.name) / "household.csv")
        cls.hours = opsd.hourly(cls.csv, "industrial1")
        cls.clean = opsd.clean_days(cls.hours)
        cls.targets = opsd.target_candidates(cls.clean)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_hourly_energy_is_the_reading_minus_the_previous_one(self):
        t = datetime(2016, 3, 20, 10, tzinfo=timezone.utc)
        col = fx.COLUMNS["industrial1"][0]
        want = _reading(self.csv, col, t) - _reading(self.csv, col, t - HOUR)
        self.assertAlmostEqual(self.hours[t][0], want, places=6)
        solar = sum(_reading(self.csv, c, t) - _reading(self.csv, c, t - HOUR) for c in fx.COLUMNS["industrial1"][1:])
        self.assertAlmostEqual(self.hours[t][1], solar, places=6)
        first = datetime(2016, 2, 29, tzinfo=timezone.utc)
        self.assertNotIn(first, self.hours)            # the first row has no previous reading

    def test_flagged_hours_are_missing(self):
        from mizan import opsd
        flagged = [t for t in self._day_hours(fx.FLAG_DAY) if t not in self.hours]
        self.assertEqual(len(flagged), 2)               # the flagged row's hour, and the next hour that starts from it
        other = opsd.hourly(self.csv, "industrial3")    # the flag names an industrial1 column only
        self.assertTrue(all(t in other for t in self._day_hours(fx.FLAG_DAY)))

    def test_negative_step_and_missing_row_unclean_the_day(self):
        for day in (fx.NEG_DAY, fx.MISSING_DAY, fx.FLAG_DAY):
            with self.subTest(day=day):
                self.assertNotIn(day, self.clean)
                for k in range(0, 8):
                    self.assertNotIn(day + timedelta(days=k), self.targets)

    def test_copied_and_straight_line_days_are_unclean(self):
        self.assertIn(fx.COPY_DAY - timedelta(days=1), self.clean)   # the original stays clean
        self.assertNotIn(fx.COPY_DAY, self.clean)
        self.assertNotIn(fx.FLAT_DAY, self.clean)
        self.assertIn(fx.FLAT_DAY + timedelta(days=1), self.clean)

    def test_dst_day_is_clean_but_not_a_target(self):
        self.assertIn(fx.DST_DAY, self.clean)
        self.assertEqual(len(self.clean[fx.DST_DAY]), 23)
        self.assertNotIn(fx.DST_DAY, self.targets)
        self.assertIn(fx.DST_DAY + timedelta(days=1), self.targets)     # its 7 previous days, the DST day included, are clean

    def test_export_gate_fails_on_an_export_hour(self):
        from mizan import opsd
        self.assertTrue(opsd.export_gate(self.hours, self.clean)["passed"])
        day = max(self.clean)
        noon = max(self.clean[day], key=lambda t: self.hours[t][1])
        hours = dict(self.hours)
        hours[noon] = (0.0, hours[noon][1])
        gate = opsd.export_gate(hours, self.clean)
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["low_import_hours"], 1)

    def test_site_base_scales_the_generated_settings(self):
        from mizan import examples, opsd
        mean = opsd.generated_mean_load()
        self.assertAlmostEqual(mean, 67.214, places=3)
        shipped = examples.settings(24)
        for factor in (1, 2):
            with self.subTest(factor=factor):
                hours, clean = self._flat_site(mean * factor, days=30)
                base = opsd.site_base(hours, clean)
                self.assertAlmostEqual(base["scale"], factor, places=9)
                for k in ("capacity", "power", "initial", "terminal"):
                    self.assertAlmostEqual(base["settings"]["assets"][k], shipped["assets"][k] * factor, places=9)
                self.assertEqual(base["settings"]["tariff"], shipped["tariff"])
                self.assertEqual((base["settings"]["risk"], base["settings"]["beta"]), (shipped["risk"], shipped["beta"]))
                self.assertAlmostEqual(base["limit_kw"], 2 * mean * factor, places=9)
                self.assertEqual(base["settings"]["assets"]["limit"], base["limit_kw"])
        with self.assertRaises(Refused) as cm:
            opsd.site_base(*self._flat_site(mean, days=27))
        self.assertEqual(cm.exception.code, "OPSD_SIZING")

    def test_history_rows_respect_the_window_and_vintage(self):
        from mizan import opsd
        origin = datetime(2016, 4, 3, 22, tzinfo=timezone.utc)   # local midnight of 2016-04-04 (CEST)
        rows = opsd.history_rows(self.hours, self.clean, origin, days=10)
        times = [datetime.fromisoformat(r["time"]) for r in rows]
        self.assertTrue(rows)
        self.assertTrue(all(origin - timedelta(days=10) <= t < origin for t in times))
        self.assertTrue(all(datetime.fromisoformat(r["known_at"]) == t + HOUR for r, t in zip(rows, times)))
        clean_hours = {t for hs in self.clean.values() for t in hs}
        self.assertTrue(set(times) <= clean_hours)
        r = rows[0]
        self.assertAlmostEqual(r["load"], sum(self.hours[times[0]]), places=9)
        self.assertAlmostEqual(r["pv"], self.hours[times[0]][1], places=9)

    def _day_hours(self, day):
        from mizan import opsd
        return opsd.local_day_hours(day)

    def _flat_site(self, load, days):
        from mizan import opsd
        hours = {}
        for k in range(days):
            for t in opsd.local_day_hours(date(2016, 6, 1) + timedelta(days=k)):
                hours[t] = (load, 0.0)
        return hours, {date(2016, 6, 1) + timedelta(days=k): opsd.local_day_hours(date(2016, 6, 1) + timedelta(days=k))
                       for k in range(days)}


class ProtocolTests(unittest.TestCase):
    def test_protocol_digest_is_sealed(self):
        from mizan import opsd
        from mizan.core.contract import digest
        self.assertEqual(digest(opsd.PROTOCOL), opsd.SEALED_DIGEST)

    def test_protocol_carries_the_spec_values(self):
        from mizan import hf_evaluation, opsd
        v = opsd.PROTOCOL["values"]
        self.assertEqual((v["lookback_days"], v["history_days"], v["chronos_context_hours"], v["sizing_days"]), (7, 300, 2048, 28))
        self.assertEqual((v["sunny_fraction_of_p99_solar"], v["low_import_fraction_of_median_load"], v["limit_factor"]), (0.2, 0.02, 2.0))
        self.assertEqual((v["block_days"], v["draws"], v["seed"], v["flat_run_hours"], v["rounding_digits"]), (7, 4000, 2026, 4, 6))
        self.assertEqual(opsd.PROTOCOL["data"]["sha256"], "15956440b27465686eae2abf5895cbc3dab80f934a891bcb2f4f944428e97d60")
        self.assertEqual(opsd.PROTOCOL["policies"], list(hf_evaluation.POLICIES_HF))
        self.assertEqual(set(opsd.PROTOCOL["rules"]), {f"{i}_" + n for i, n in enumerate(
            ["hourly_energy", "load", "clean_day", "no_export_gate", "target_day", "forecast_inputs", "battery_and_tariff",
             "carried_state", "measures", "bootstrap_and_pooling", "readings", "refusals"], start=1)})
        # The data rules read their values from the protocol, so a protocol change changes behaviour with the digest.
        self.assertEqual((opsd.LOOKBACK_DAYS, opsd.SIZING_DAYS, opsd.FLAT_RUN, opsd.LIMIT_FACTOR), (7, 28, 4, 2.0))
        for word in ("euro", "EUR", "€", "saving", "money"):
            self.assertNotIn(word, str(opsd.PROTOCOL).replace("Europe", ""))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source = fx.make_opsd_csv(base / "source.csv", days=3)
        self.out = base / "out"
        self.size = self.source.stat().st_size
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, **kw):
        from scripts.fetch_opsd import main
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--out", str(self.out), "--url", self.source.as_uri()], **kw)

    def test_fetch_accepts_the_pinned_file(self):
        from mizan import opsd
        self.assertEqual(self.fetch(file_bytes=self.size, file_sha256=self.sha), 0)
        self.assertEqual((self.out / opsd.FILE_NAME).read_bytes(), self.source.read_bytes())
        self.assertEqual(self.fetch(file_bytes=self.size, file_sha256=self.sha), 0)   # present and matching: verified

    def test_fetch_refuses_a_changed_file(self):
        from mizan import opsd
        self.assertNotEqual(self.fetch(file_bytes=self.size, file_sha256="0" * 64), 0)
        self.assertEqual([p.name for p in self.out.iterdir()] if self.out.exists() else [], [])
        self.assertFalse((self.out / opsd.FILE_NAME).exists())

    def test_pinned_identity_is_the_approved_file(self):
        from mizan import opsd
        self.assertEqual(opsd.FILE_BYTES, 15654559)
        self.assertEqual(opsd.FILE_SHA256, "15956440b27465686eae2abf5895cbc3dab80f934a891bcb2f4f944428e97d60")
        self.assertTrue(opsd.URL.endswith("/household_data/2020-04-15/household_data_60min_singleindex.csv"))


if __name__ == "__main__":
    unittest.main()
