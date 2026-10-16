"""All tests here are deterministic unit tests. No network, no model/API calls.

They prove the rule engine and bookkeeping behave as designed. They do NOT prove
the starter rules match the platform's real review behaviour - that needs the
team's labelled outcomes (see store.agreement_report).
"""
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from creative_studio.compliance import (
    ComplianceChecker,
    RuleSet,
    RulesError,
    VERDICT_BLOCK,
    VERDICT_NONE,
    VERDICT_REVIEW,
)
from creative_studio.compliance import cli, store
from creative_studio.compliance.engine import DEFAULT_RULES_PATH

FIXED = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def checker():
    return ComplianceChecker(clock=lambda: FIXED)


def rule_ids(result):
    return {f.rule_id for f in result.findings}


class RuleFileTests(unittest.TestCase):
    def test_default_rules_load_and_are_well_formed(self):
        rs = RuleSet.load()
        ids = [r.id for r in rs.rules] + [r.id for r in rs.context_rules]
        self.assertEqual(len(ids), len(set(ids)), "rule ids must be unique")
        self.assertTrue(rs.version)
        for r in rs.rules:
            self.assertIn(r.category, rs.categories)

    def test_bad_severity_rejected(self):
        data = json.loads(Path(DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
        data["rules"][0]["severity"] = "purple"
        with self.assertRaises(RulesError):
            RuleSet(data)

    def test_duplicate_id_rejected(self):
        data = json.loads(Path(DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
        data["rules"].append(dict(data["rules"][0]))
        with self.assertRaises(RulesError):
            RuleSet(data)

    def test_unknown_category_rejected(self):
        data = json.loads(Path(DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
        data["rules"][0]["category"] = "made up"
        with self.assertRaises(RulesError):
            RuleSet(data)

    def test_bad_regex_rejected(self):
        data = json.loads(Path(DEFAULT_RULES_PATH).read_text(encoding="utf-8"))
        data["rules"][0]["patterns"] = ["(unclosed"]
        with self.assertRaises(RulesError):
            RuleSet(data)


class DetectionTests(unittest.TestCase):
    def test_cure_claim_is_red_medical(self):
        r = checker().check("This capsule cures fatty liver in two weeks.")
        self.assertEqual(r.verdict, VERDICT_BLOCK)
        self.assertIn("MED-001", rule_ids(r))
        self.assertIn("医疗/药品/限制行业", r.categories())

    def test_weight_loss_and_guarantee(self):
        r = checker().check("Lose 10 pounds fast, guaranteed.")
        self.assertTrue({"EXG-001", "EXG-002"} <= rule_ids(r))
        self.assertEqual(r.verdict, VERDICT_BLOCK)

    def test_burn_fat_phrase_is_weight_loss(self):
        # Regression: "burn fat fast" was missed by the first rule version.
        r = checker().check("This tea helps burn fat fast.")
        self.assertIn("EXG-002", rule_ids(r))
        self.assertEqual(r.verdict, VERDICT_BLOCK)

    def test_chinese_claims(self):
        r = checker().check("三天排毒，修复肝细胞，保证有效")
        self.assertTrue({"MED-004", "MED-003", "EXG-001"} <= rule_ids(r))

    def test_replace_medication(self):
        r = checker().check("Stop taking your medication, this works better.")
        self.assertIn("MED-002", rule_ids(r))

    def test_impersonating_professional(self):
        r = checker().check("As a doctor, I tell all my patients to take this.")
        self.assertIn("MED-006", rule_ids(r))

    def test_personal_medical_testimonial_is_flagged(self):
        # Regression: a lab-value / diagnosis story slipped through the first rule version.
        for text in (
            "My doctor told me my liver enzymes were high, so I started taking milk thistle.",
            "I was diagnosed last year and now my cholesterol dropped.",
            "医生跟我说转氨酶偏高，后来我开始吃这个",
        ):
            r = checker().check(text)
            self.assertIn("MED-009", rule_ids(r), text)
            self.assertEqual(r.verdict, VERDICT_REVIEW, text)

    def test_testimonial_rule_has_no_obvious_false_positive(self):
        r = checker().check("I take one capsule every morning with breakfast.")
        self.assertNotIn("MED-009", rule_ids(r))

    def test_yellow_only_is_needs_review(self):
        r = checker().check("Many people love the transformation, and they use it daily. Consult your doctor first.")
        self.assertEqual(r.verdict, VERDICT_REVIEW)
        self.assertEqual({f.severity for f in r.findings}, {"yellow"})

    def test_clean_text_is_not_called_compliant(self):
        r = checker().check("The capsule comes in a green bottle with 60 count.")
        self.assertEqual(r.verdict, VERDICT_NONE)
        self.assertTrue(r.human_approval_required)
        self.assertIn("Not a compliance approval", r.note)
        self.assertNotIn("compliant", r.verdict)

    def test_case_insensitive(self):
        r = checker().check("MIRACLE results!")
        self.assertIn("EXG-001", rule_ids(r))

    def test_finding_reports_exact_span(self):
        text = "Totally fine intro. It is a miracle product."
        r = checker().check(text)
        f = next(x for x in r.findings if x.rule_id == "EXG-001")
        self.assertEqual(text[f.start:f.end].lower(), f.matched_text.lower())

    def test_context_rule_fires_without_responsible_wording(self):
        r = checker().check("It supports liver health every day.")
        self.assertIn("INF-001", rule_ids(r))
        f = next(x for x in r.findings if x.rule_id == "INF-001")
        self.assertEqual(f.category, "健康信息责任表达")
        self.assertIn("待核实", f.suggestion)

    def test_context_rule_silent_with_responsible_wording(self):
        r = checker().check("It supports liver health. Consult your doctor; results may vary.")
        self.assertNotIn("INF-001", rule_ids(r))

    def test_suggestions_never_help_evade(self):
        r = checker().check("This is a miracle detox that cures cirrhosis, guaranteed.")
        for f in r.findings:
            self.assertTrue(f.suggestion)
            self.assertNotRegex(f.suggestion.lower(), r"avoid detection|bypass|get around|evade|不被(审核|检测)")


class ShotJsonTests(unittest.TestCase):
    SHOTS = {
        "shots": [
            {"shot_id": "s1", "story_description": "Woman opens a bottle.", "dialogue": "I feel great."},
            {"shot_id": "s2", "voice_over": "It reverses liver damage.", "image_prompt": "bottle on table"},
            {"shot_id": "s3", "on_screen_text": ["100% safe", "Free shipping"]},
        ]
    }

    def test_findings_carry_shot_id_and_field(self):
        r = checker().check(self.SHOTS, content_id="V1")
        by_shot = {(f.shot_id, f.field, f.rule_id) for f in r.findings}
        self.assertIn(("s2", "voice_over", "MED-001"), by_shot)
        self.assertIn(("s3", "on_screen_text", "MIS-001"), by_shot)
        self.assertFalse(any(f.shot_id == "s1" for f in r.findings if f.rule_id != "INF-001"))
        self.assertEqual(r.content_id, "V1")

    def test_bare_list_and_single_shot_accepted(self):
        r1 = checker().check(self.SHOTS["shots"])
        r2 = checker().check({"shot_id": "x", "dialogue": "guaranteed"})
        self.assertEqual(r1.verdict, VERDICT_BLOCK)
        self.assertEqual(r2.verdict, VERDICT_BLOCK)

    def test_missing_shot_id_gets_placeholder(self):
        r = checker().check([{"dialogue": "miracle"}])
        self.assertEqual(r.findings[0].shot_id, "shot-1")

    def test_bad_input_types_raise(self):
        with self.assertRaises(ValueError):
            checker().check(42)
        with self.assertRaises(ValueError):
            checker().check(["not a shot"])
        with self.assertRaises(ValueError):
            checker().check([{"dialogue": 5}])

    def test_result_is_json_serialisable(self):
        r = checker().check(self.SHOTS, content_id="V1")
        d = json.loads(json.dumps(r.to_dict(), ensure_ascii=False))
        self.assertEqual(d["rules_version"], RuleSet.load().version)
        self.assertEqual(d["checked_at"], FIXED.isoformat())


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")
        self.chk = checker()

    def run_and_label(self, cid, text, status, reasons=()):
        store.save_run(self.conn, self.chk.check(text, content_id=cid))
        store.record_outcome(self.conn, cid, status, reasons)

    def test_agreement_report_counts(self):
        self.run_and_label("a", "It cures fatty liver.", "rejected", ["医疗/药品/限制行业"])   # TP, caught
        self.run_and_label("b", "Guaranteed weight loss.", "rejected", ["夸大/减重/财务效果声明"])  # TP, caught
        self.run_and_label("c", "A bottle with 60 capsules.", "rejected", ["性暗示/裸露/敏感身体画面"])  # FN
        self.run_and_label("d", "Many love the transformation. Consult your doctor.", "approved")  # FP
        self.run_and_label("e", "A bottle with 60 capsules.", "approved")  # TN
        rep = store.agreement_report(self.conn)
        self.assertEqual(rep["labelled_items"], 5)
        self.assertEqual(rep["flagged_and_rejected"], 2)
        self.assertEqual(rep["flagged_but_approved"], 1)
        self.assertEqual(rep["not_flagged_but_rejected"], 1)
        self.assertEqual(rep["not_flagged_and_approved"], 1)
        self.assertAlmostEqual(rep["precision"], 2 / 3, places=3)
        self.assertAlmostEqual(rep["recall"], 2 / 3, places=3)
        self.assertEqual(rep["platform_rejection_rate"], 0.6)
        self.assertEqual(rep["per_platform_reason"]["医疗/药品/限制行业"]["catch_rate"], 1.0)
        self.assertEqual(rep["per_platform_reason"]["性暗示/裸露/敏感身体画面"]["catch_rate"], 0.0)

    def test_unlabelled_runs_are_ignored(self):
        store.save_run(self.conn, self.chk.check("miracle", content_id="x"))
        self.assertEqual(store.agreement_report(self.conn)["labelled_items"], 0)

    def test_empty_report_has_no_division_errors(self):
        rep = store.agreement_report(self.conn)
        self.assertIsNone(rep["precision"])
        self.assertIsNone(rep["recall"])

    def test_latest_run_and_outcome_win(self):
        store.save_run(self.conn, self.chk.check("miracle", content_id="z"))
        store.save_run(self.conn, self.chk.check("plain bottle", content_id="z"))
        store.record_outcome(self.conn, "z", "rejected", ["其他"])
        store.record_outcome(self.conn, "z", "approved")
        rep = store.agreement_report(self.conn)
        self.assertEqual(rep["labelled_items"], 1)
        self.assertEqual(rep["not_flagged_and_approved"], 1)

    def test_invalid_outcomes_rejected(self):
        with self.assertRaises(ValueError):
            store.record_outcome(self.conn, "q", "maybe")
        with self.assertRaises(ValueError):
            store.record_outcome(self.conn, "q", "approved", ["医疗/药品/限制行业"])

    def test_chinese_text_roundtrips_through_sqlite(self):
        store.save_run(self.conn, self.chk.check("三天排毒", content_id="zh"))
        row = self.conn.execute("SELECT result_json FROM runs WHERE content_id='zh'").fetchone()
        self.assertIn("排毒", json.loads(row["result_json"])["findings"][0]["matched_text"])


class CliTests(unittest.TestCase):
    def test_check_record_and_report_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp, "s.txt")
            script.write_text("This miracle capsule cures cirrhosis.", encoding="utf-8")
            db = str(Path(tmp, "p.db"))
            out = io.StringIO()
            code = cli.main(["check", "--input", str(script), "--content-id", "V9", "--db", db], out=out)
            self.assertEqual(code, 2)  # block_recommended -> non-zero exit
            self.assertEqual(json.loads(out.getvalue())["verdict"], VERDICT_BLOCK)

            out = io.StringIO()
            self.assertEqual(cli.main(["outcome", "--db", db, "--content-id", "V9", "--status", "rejected",
                                       "--reason", "医疗/药品/限制行业"], out=out), 0)
            out = io.StringIO()
            self.assertEqual(cli.main(["report", "--db", db], out=out), 0)
            rep = json.loads(out.getvalue())
            self.assertEqual(rep["flagged_and_rejected"], 1)

    def test_check_json_shot_file_clean_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "shots.json")
            f.write_text(json.dumps({"shots": [{"shot_id": "s1", "dialogue": "Hello there."}]}), encoding="utf-8")
            out = io.StringIO()
            self.assertEqual(cli.main(["check", "--input", str(f)], out=out), 0)
            self.assertEqual(json.loads(out.getvalue())["verdict"], VERDICT_NONE)


if __name__ == "__main__":
    unittest.main()
