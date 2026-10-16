"""Mock-only tests (no network, no real model calls). They verify the data contract,
adapter wiring and job safety rules - not real model quality."""
import json
import unittest

from pydantic import ValidationError

from creative_studio.adapters.base import RemoteJobState
from creative_studio.adapters.mock import MockImageGenerator, MockVideoAnalyzer, MockVideoGenerator
from creative_studio.jobs.video_jobs import BudgetExceeded, VideoJobManager, idempotency_key
from creative_studio.shots.schema import GenerationStatus, Shot, Storyboard, storyboard_json_schema


def shot(**kw):
    base = dict(project_id="p1", version_id="v1", shot_id="s001", shot_order=1, start_time=0, duration=5)
    base.update(kw)
    return base


class SchemaTests(unittest.TestCase):
    def test_valid_storyboard(self):
        sb = Storyboard(project_id="p1", version_id="v1",
                        shots=[shot(), shot(shot_id="s002", shot_order=2, start_time=5, duration=3.5)])
        self.assertEqual(sb.total_duration(), 8.5)
        self.assertEqual([s.shot_id for s in sb.ordered()], ["s001", "s002"])

    def test_duplicate_shot_id_and_order_rejected(self):
        with self.assertRaises(ValidationError):
            Storyboard(project_id="p1", version_id="v1", shots=[shot(), shot(shot_order=2)])
        with self.assertRaises(ValidationError):
            Storyboard(project_id="p1", version_id="v1", shots=[shot(), shot(shot_id="s002")])

    def test_field_rules(self):
        for bad in (dict(duration=0), dict(start_time=-1), dict(shot_order=0),
                    dict(character_ids=["c1", "c1"]), dict(unexpected_field=1),
                    dict(generation_method="reuse_licensed_asset")):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                Shot(**shot(**bad))

    def test_evidence_range_and_unknown_marks(self):
        with self.assertRaises(ValidationError):
            Shot(**shot(source_evidence=[{"source_video_id": "r1", "start_time": 4, "end_time": 2}]))
        s = Shot(**shot(confidence_or_unknown=[{"field": "dialogue", "reason": "inaudible"}]))
        self.assertEqual(s.confidence_or_unknown[0].field, "dialogue")

    def test_shot_must_match_storyboard_version(self):
        with self.assertRaises(ValidationError):
            Storyboard(project_id="p1", version_id="v1", shots=[shot(version_id="v9")])

    def test_new_version_never_mutates_history(self):
        sb = Storyboard(project_id="p1", version_id="v1", shots=[shot(dialogue="old")])
        edited = [dict(sb.shots[0].model_dump(), dialogue="new")]
        v2 = sb.new_version("v2", created_by="editor-a", shots=edited)
        self.assertEqual(sb.shots[0].dialogue, "old")
        self.assertEqual(v2.shots[0].dialogue, "new")
        self.assertEqual(v2.parent_version_id, "v1")
        self.assertEqual(v2.shots[0].version_id, "v2")
        with self.assertRaises(ValueError):
            sb.new_version("v1", created_by="x")

    def test_json_schema_exports(self):
        schema = storyboard_json_schema()
        json.dumps(schema)
        self.assertIn("shots", schema["properties"])


class MockAdapterTests(unittest.TestCase):
    def test_mock_analyzer_output_validates_and_is_flagged_mock(self):
        res = MockVideoAnalyzer(shot_seconds=5).analyze(project_id="p1", version_id="v1",
                                                         source_video_id="ref1", video_uri="x", duration_s=32)
        self.assertTrue(res.meta.is_mock)
        sb = Storyboard(**res.storyboard)
        self.assertEqual(len(sb.shots), 7)
        self.assertAlmostEqual(sb.total_duration(), 32.0)
        self.assertTrue(all(s.confidence_or_unknown for s in sb.shots))

    def test_mock_image_is_deterministic(self):
        g = MockImageGenerator()
        a = g.generate(prompt="p", reference_uris=["r"], width=720, height=1280)
        b = g.generate(prompt="p", reference_uris=["r"], width=720, height=1280)
        self.assertEqual(a.uri, b.uri)
        self.assertTrue(a.meta.is_mock)


class JobManagerTests(unittest.TestCase):
    def setUp(self):
        self.shot = Shot(**shot(video_prompt="a woman opens a bottle"))

    def test_happy_path(self):
        gen = MockVideoGenerator(polls_until_done=2)
        mgr = VideoJobManager(gen)
        att = mgr.submit(self.shot)
        self.assertEqual(att.status, GenerationStatus.SUBMITTED)
        self.assertEqual(mgr.poll(self.shot).status, GenerationStatus.RUNNING)
        done = mgr.poll(self.shot)
        self.assertEqual(done.status, GenerationStatus.SUCCEEDED)
        self.assertTrue(done.output_uri.startswith("mock://"))
        self.assertTrue(done.is_mock)

    def test_unknown_submit_polls_instead_of_paying_twice(self):
        gen = MockVideoGenerator(polls_until_done=1, fail_submit_ambiguously=1)
        mgr = VideoJobManager(gen)
        att = mgr.submit(self.shot)
        self.assertEqual(att.status, GenerationStatus.UNKNOWN)
        with self.assertRaises(RuntimeError):  # no new attempt while one is unresolved
            mgr.submit(self.shot)
        done = mgr.poll(self.shot)
        self.assertEqual(done.status, GenerationStatus.SUCCEEDED)
        self.assertEqual(gen.accepted_jobs, 1, "provider must have run exactly one paid job")
        self.assertEqual(gen.submit_calls, 1)

    def test_unknown_then_not_found_resubmits_same_key(self):
        gen = MockVideoGenerator()
        mgr = VideoJobManager(gen)
        # simulate: submit lost before reaching the provider
        att = mgr.submit(self.shot)
        gen.jobs.clear()
        att.status = GenerationStatus.UNKNOWN
        mgr.poll(self.shot)
        self.assertEqual(att.status, GenerationStatus.SUBMITTED)
        self.assertIn(att.idempotency_key, gen.jobs)
        self.assertEqual(len(mgr.attempts[self.shot.shot_id]), 1)

    def test_failure_is_isolated_per_shot_and_retry_gets_new_key(self):
        gen = MockVideoGenerator(fail_jobs=True)
        mgr = VideoJobManager(gen)
        other = Shot(**shot(shot_id="s002", shot_order=2))
        mgr.submit(self.shot); mgr.submit(other)
        self.assertEqual(mgr.poll(self.shot).status, GenerationStatus.FAILED)
        gen.fail_jobs = False
        self.assertEqual(mgr.poll(other).status, GenerationStatus.SUCCEEDED)
        retry = mgr.submit(self.shot)
        self.assertEqual(retry.attempt_no, 2)
        self.assertEqual(retry.idempotency_key, idempotency_key("p1", "v1", "s001", 2))

    def test_budget_cap_blocks_submission(self):
        mgr = VideoJobManager(MockVideoGenerator(), budget_usd=1.0)
        with self.assertRaises(BudgetExceeded):
            mgr.submit(self.shot, estimate_usd=1.5)
        self.assertEqual(mgr.attempts, {})


if __name__ == "__main__":
    unittest.main()
