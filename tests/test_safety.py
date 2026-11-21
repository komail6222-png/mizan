"""Release 3.0: deterministic simulation of the real broker and the metamorphic safety suite (small runs)."""
import unittest

from mizan import safety, simulation


class SimulationTests(unittest.TestCase):
    def test_seeded_schedules_hold_every_invariant(self):
        report = simulation.explore(range(25), 40)
        self.assertEqual(report["violations"], 0, report["failures"])
        self.assertGreater(report["crashes"], 0)
        self.assertGreater(report["publishes"], 0)
        self.assertGreater(report["stale_publishes_refused"], 0)

    def test_schedules_are_reproducible_from_their_seed(self):
        a, b = simulation.Simulation(7, 30), simulation.Simulation(7, 30)
        ra, rb = a.run(), b.run()
        self.assertEqual(a.trace, b.trace)
        self.assertEqual({k: v for k, v in ra.items()}, {k: v for k, v in rb.items()})

    def test_broken_fencing_is_caught(self):
        # Seeds found by scripts/safety_suite.py; each one exposes the named mutant.
        for name, seeds in [("publication without epoch fence", [12, 43]), ("publication without cancellation fence", [3, 6])]:
            with self.subTest(mutant=name):
                cls = simulation.MUTANTS[name]
                self.assertTrue(any(not simulation.Simulation(seed, 40, cls).run()["ok"] for seed in seeds))


class MetamorphicTests(unittest.TestCase):
    def test_sanad_relations_hold(self):
        report = safety.sanad_suite(60, seed=5)
        self.assertEqual(report["violations"], 0, report["examples"])
        self.assertGreater(report["checks"]["R7_monotone"], 0)

    def test_sanad_suite_catches_broken_engines(self):
        for name, result in safety.sanad_mutants(60, seed=6).items():
            with self.subTest(mutant=name):
                self.assertTrue(result["caught"], result)

    def test_basira_properties_and_exceedance_formula(self):
        report = safety.basira_suite(300, 4000, seed=7)
        self.assertEqual(report["violations"], 0)
        self.assertLess(report["max_abs_z"], 4.5)

    def test_rasid_verifier_is_complete_and_sound_on_random_problems(self):
        report = safety.rasid_suite(12, seed=8)
        self.assertEqual(report["solver_plans_verified"], 12)
        self.assertEqual(report["mutations_rejected"], report["mutations_attempted"])


if __name__ == "__main__":
    unittest.main()
