"""Tests for the TabCount scoring engine (main.py).

Stdlib unittest only. Run from the repo root:

    python3 -m unittest discover -s tests -v
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


class TestPercentileCurve(unittest.TestCase):
    def test_anchors_are_hit(self):
        for count, pct in main.PERCENTILE_ANCHORS:
            self.assertAlmostEqual(main.percentile(count), pct, places=6)

    def test_monotone_increasing(self):
        values = [main.percentile(n) for n in range(1, 501)]
        for a, b in zip(values, values[1:]):
            self.assertLessEqual(a, b)

    def test_below_first_anchor_scales_from_zero(self):
        self.assertAlmostEqual(main.percentile(2), 5.0, places=6)
        self.assertGreater(main.percentile(1), 0.0)

    def test_tail_flattens_below_100(self):
        self.assertLess(main.percentile(500), 100.0)
        self.assertGreater(main.percentile(500), 99.0)

    def test_median_is_17(self):
        self.assertAlmostEqual(main.percentile(17), 50.0, places=6)


class TestSurprise(unittest.TestCase):
    def test_zero_at_median(self):
        self.assertAlmostEqual(main.surprise(17), 0.0, places=6)

    def test_zero_below_median(self):
        self.assertEqual(main.surprise(1), 0.0)
        self.assertEqual(main.surprise(9), 0.0)

    def test_monotone_above_median(self):
        values = [main.surprise(n) for n in range(17, 501)]
        for a, b in zip(values, values[1:]):
            self.assertLessEqual(a, b)

    def test_p90_is_surprising(self):
        # 62 tabs is the 90th percentile: surprise should be high.
        self.assertGreater(main.surprise(62), 0.7)

    def test_high_tail_near_one(self):
        self.assertGreater(main.surprise(400), 0.95)


class TestPlausibility(unittest.TestCase):
    def test_near_one_in_lived_in_range(self):
        for n in (17, 62, 89, 137):
            self.assertGreater(main.plausibility(n), 0.8)

    def test_decays_past_p99(self):
        self.assertLess(main.plausibility(300), main.plausibility(164))
        self.assertLess(main.plausibility(500), 0.05)

    def test_monotone_decreasing(self):
        values = [main.plausibility(n) for n in range(1, 501)]
        for a, b in zip(values, values[1:]):
            self.assertGreaterEqual(a, b)


class TestHonesty(unittest.TestCase):
    def test_round_10_penalised_below_neighbours(self):
        # 100 must score below its non-round neighbours 99 and 101.
        self.assertLess(main.honesty(100)[0], main.honesty(99)[0])
        self.assertLess(main.honesty(100)[0], main.honesty(101)[0])

    def test_round_5_penalised_below_neighbours(self):
        self.assertLess(main.honesty(95)[0], main.honesty(97)[0])

    def test_repeated_digits_penalised(self):
        self.assertLess(main.honesty(111)[0], main.honesty(113)[0])
        self.assertLess(main.honesty(77)[0], main.honesty(79)[0])

    def test_sequential_digits_penalised(self):
        self.assertLess(main.honesty(123)[0], main.honesty(127)[0])

    def test_palindrome_penalised(self):
        self.assertLess(main.honesty(121)[0], main.honesty(127)[0])

    def test_cliche_numbers_penalised(self):
        for cliche in (42, 69, 100, 666):
            self.assertLess(main.honesty(cliche)[0], 0.8)

    def test_specific_odd_number_gets_full_marks(self):
        score, reasons = main.honesty(137)
        self.assertEqual(score, 1.0)
        self.assertIn("specific odd number", reasons)

    def test_honesty_stays_in_unit_interval(self):
        for n in range(1, 501):
            s, _ = main.honesty(n)
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)


class TestLifeHint(unittest.TestCase):
    def test_peaks_at_100(self):
        self.assertAlmostEqual(main.life_hint(100), 1.0, places=6)
        self.assertLess(main.life_hint(17), main.life_hint(100))
        self.assertLess(main.life_hint(400), main.life_hint(100))

    def test_busy_band_above_fresh_browser(self):
        self.assertGreater(main.life_hint(100), main.life_hint(3))


class TestScoring(unittest.TestCase):
    def test_winner_inside_plausible_band(self):
        best = main.best_count()
        self.assertGreaterEqual(best["count"], 60)
        self.assertLessEqual(best["count"], 165)

    def test_winner_is_not_a_cliche_number(self):
        best = main.best_count()
        self.assertNotIn(best["count"], main.CLICHE_NUMBERS)

    def test_winner_is_not_round(self):
        best = main.best_count()
        self.assertNotEqual(best["count"] % 10, 0)
        self.assertNotEqual(best["count"] % 5, 0)

    def test_absurd_counts_score_below_the_band(self):
        best = main.best_count()
        for n in (300, 400, 500):
            self.assertLess(main.score(n)["total"], best["total"])

    def test_typical_counts_score_below_the_winner(self):
        best = main.best_count()
        for n in (8, 17, 23):
            self.assertLess(main.score(n)["total"], best["total"])

    def test_total_is_weighted_sum_of_components(self):
        result = main.score(137)
        expected = (
            main.WEIGHT_SURPRISE * result["surprise"]
            + main.WEIGHT_PLAUSIBILITY * result["plausibility"]
            + main.WEIGHT_HONESTY * result["honesty"]
            + main.WEIGHT_LIFE * result["life_hint"]
        )
        self.assertAlmostEqual(result["total"], expected, places=9)

    def test_tie_break_prefers_lowest_count(self):
        # With a flat scoring range the argmax must be deterministic.
        first = main.best_count()
        second = main.best_count()
        self.assertEqual(first, second)


class TestRevisionPass(unittest.TestCase):
    """Edge cases found while running v1 (revision pass)."""

    def test_clean_odd_beats_clean_even(self):
        # v1 bug: the odd bonus was clamped away, so 102 (even) tied
        # with 103 (odd). The base is now 0.90, so the bonus is real.
        self.assertGreater(main.honesty(103)[0], main.honesty(102)[0])
        self.assertEqual(main.honesty(103)[0], 1.0)
        self.assertEqual(main.honesty(102)[0], 0.90)

    def test_winner_is_odd_with_mixed_digits(self):
        best = main.best_count()
        self.assertEqual(best["count"] % 2, 1)
        text = str(best["count"])
        self.assertGreater(len(set(text)), 1)

    def test_winner_beats_both_immediate_neighbours(self):
        best = main.best_count()
        n = best["count"]
        self.assertGreater(best["total"], main.score(n - 1)["total"])
        self.assertGreater(best["total"], main.score(n + 1)["total"])

    def test_winner_beats_typical_count_by_a_margin(self):
        best = main.best_count()
        self.assertGreater(best["total"] - main.score(17)["total"], 0.2)

    def test_percentile_zero_is_zero(self):
        self.assertEqual(main.percentile(0), 0.0)
        self.assertEqual(main.percentile(-5), 0.0)

    def test_honesty_never_negative_after_stacked_penalties(self):
        # 1000 is cliche AND a multiple of 10; 111 is repeated AND a
        # palindrome; both must stay inside [0, 1].
        for n in (1000, 111, 777, 100):
            s, _ = main.honesty(n)
            self.assertGreaterEqual(s, 0.0)


class TestCLI(unittest.TestCase):
    def run_main(self, env_extra=None):
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run(
            [sys.executable, os.path.join(repo_root, "main.py")],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def test_runs_and_writes_digits_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_main({"TABCOUNT_OUTPUT_DIR": tmp})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(os.path.join(tmp, "entry.txt"), encoding="utf-8") as f:
                content = f.read()
            self.assertTrue(content.isdigit(), content)
            self.assertTrue(1 <= len(content) <= 5)
            self.assertEqual(content, str(main.best_count()["count"]))

    def test_deterministic_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_main({"TABCOUNT_OUTPUT_DIR": tmp})
            with open(os.path.join(tmp, "entry.txt"), "rb") as f:
                first = f.read()
            self.run_main({"TABCOUNT_OUTPUT_DIR": tmp})
            with open(os.path.join(tmp, "entry.txt"), "rb") as f:
                second = f.read()
            self.assertEqual(first, second)

    def test_stdout_names_count_and_breakdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_main({"TABCOUNT_OUTPUT_DIR": tmp})
            self.assertIn("count=", proc.stdout)
            self.assertIn("score total=", proc.stdout)
            self.assertIn("surprise=", proc.stdout)
            self.assertIn("plausibility=", proc.stdout)
            self.assertIn("honesty=", proc.stdout)
            self.assertIn("life_hint=", proc.stdout)

    def test_rejects_arguments(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, os.path.join(repo_root, "main.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 2)

    def test_no_trailing_newline_in_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_main({"TABCOUNT_OUTPUT_DIR": tmp})
            with open(os.path.join(tmp, "entry.txt"), "rb") as f:
                raw = f.read()
            self.assertFalse(raw.endswith(b"\n"))
            self.assertEqual(raw.decode("utf-8").strip(), raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
