"""Offline tests: model aliases and same-family upgrades follow the key's model list. No network."""
import argparse
import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from PIL import Image

SPEC = importlib.util.spec_from_file_location("apinebula_image_models", Path(__file__).parents[1] / "scripts/apinebula_image.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class FamilyTests(unittest.TestCase):
    def test_family_parsing(self):
        self.assertEqual(m.model_family("gpt-image-2.5"), ("gpt-image-{v}", (2, 5), True))
        self.assertEqual(m.model_family("gpt-image-3"), ("gpt-image-{v}", (3,), True))
        self.assertEqual(m.model_family("gemini-3.1-flash-image-preview"), ("gemini-{v}-flash-image", (3, 1), False))
        self.assertEqual(m.model_family("gemini-3-pro-image-preview"), ("gemini-{v}-pro-image", (3,), False))
        self.assertEqual(m.model_family("gpt-image-2-4k")[0], "gpt-image-{v}-4k")
        self.assertIsNone(m.model_family("dall-e"))

    def test_newest_prefers_higher_version_then_stable(self):
        self.assertEqual(m.newest_in_family("gpt-image-{v}", ["gpt-image-2.5", "gpt-image-2.10", "gpt-image-2.9"]), "gpt-image-2.10")
        self.assertEqual(m.newest_in_family("gpt-image-{v}", ["gpt-image-2.5", "gpt-image-3"]), "gpt-image-3")
        # A stable release beats a newer preview; previews are used only when nothing stable exists.
        self.assertEqual(m.newest_in_family("gemini-{v}-flash-image",
                                            ["gemini-3.1-flash-image", "gemini-3.2-flash-image-preview"]), "gemini-3.1-flash-image")
        self.assertEqual(m.newest_in_family("gemini-{v}-pro-image", ["gemini-3-pro-image-preview"]), "gemini-3-pro-image-preview")
        # Variants with other suffixes are separate families.
        self.assertEqual(m.newest_in_family("gpt-image-{v}", ["gpt-image-2.5", "gpt-image-2-4k", "gpt-image-9-mini"]), "gpt-image-2.5")
        self.assertIsNone(m.newest_in_family("gpt-image-{v}", ["gemini-3.1-flash-image"]))

    def test_aliases_and_synonyms(self):
        self.assertEqual(m.canonical_alias("GPT-Image"), "gpt-image")
        self.assertEqual(m.canonical_alias("gemini-pro"), "nanobanana-pro")
        self.assertEqual(m.canonical_alias("nano-banana"), "nanobanana")
        self.assertIsNone(m.canonical_alias("gpt-image-2.5"))
        self.assertEqual(m.group_for("nanobanana-pro"), "nanobanana")
        self.assertEqual(m.group_for("gemini-flash"), "nanobanana")
        self.assertEqual(m.group_for("gpt-image"), "gpt-image")


class ResolveTests(unittest.TestCase):
    def test_alias_follows_provider_upgrade(self):
        self.assertEqual(m.resolve_model("gpt-image", ["gpt-image-2.5"]), ("gpt-image-2.5", "latest", None))
        self.assertEqual(m.resolve_model("gpt-image", ["gpt-image-2.6"])[:2], ("gpt-image-2.6", "latest"))
        self.assertEqual(m.resolve_model("gpt-image", ["gpt-image-2.5", "gpt-image-2.6"])[0], "gpt-image-2.6")
        self.assertEqual(m.resolve_model("nanobanana", ["gemini-3.1-flash-image", "gemini-4-flash-image", "gemini-3-pro-image-preview"])[0],
                         "gemini-4-flash-image")
        self.assertEqual(m.resolve_model("nanobanana-pro", ["gemini-3.1-flash-image", "gemini-3-pro-image-preview"])[0],
                         "gemini-3-pro-image-preview")

    def test_exact_id_is_kept_while_available(self):
        self.assertEqual(m.resolve_model("gpt-image-2.5", ["gpt-image-2.5", "gpt-image-2.6"]), ("gpt-image-2.5", "exact", None))

    def test_retired_id_moves_to_same_family(self):
        model, resolution, note = m.resolve_model("gpt-image-2.5", ["gpt-image-2.6", "gpt-image-2-4k"])
        self.assertEqual((model, resolution), ("gpt-image-2.6", "substituted"))
        self.assertIn("gpt-image-2.5", note)
        self.assertEqual(m.resolve_model("gemini-3.1-flash-image-preview", ["gemini-3.1-flash-image"])[:2],
                         ("gemini-3.1-flash-image", "substituted"))

    def test_strict_refuses_substitution(self):
        with self.assertRaises(m.UserError) as err:
            m.resolve_model("gpt-image-2.5", ["gpt-image-2.6"], strict=True)
        self.assertIn("gpt-image-2.6", str(err.exception))
        self.assertIn("No request sent", str(err.exception))

    def test_unrelated_or_empty_family_fails_before_request(self):
        with self.assertRaises(m.UserError):
            m.resolve_model("gpt-image-2.5", ["gemini-3.1-flash-image"])
        with self.assertRaises(m.UserError):
            m.resolve_model("gpt-image", ["gemini-3.1-flash-image"])
        with self.assertRaises(m.UserError):
            m.resolve_model("dall-e", ["gpt-image-2.6"])

    def test_unreadable_list_falls_back_without_blocking(self):
        self.assertEqual(m.resolve_model("gpt-image", None)[:2], ("gpt-image-2.5", "unchecked"))
        self.assertEqual(m.resolve_model("nanobanana", None)[:2], ("gemini-3.1-flash-image", "unchecked"))
        self.assertEqual(m.resolve_model("gpt-image-2.6", None)[:2], ("gpt-image-2.6", "unchecked"))


class ListModelsTests(unittest.TestCase):
    def fake_session(self, response):
        client = MagicMock()
        client.get.return_value = response
        cm = MagicMock()
        cm.__enter__.return_value = client
        cm.__exit__.return_value = False
        return client, (lambda direct=False: cm)

    def test_reads_ids_with_group_key(self):
        r = MagicMock(status_code=200)
        r.json.return_value = {"data": [{"id": "gpt-image-2.6"}, {"id": "gpt-image-2.6"}, {"x": 1}, "bad"]}
        client, factory = self.fake_session(r)
        with patch.object(m, "get_key", return_value="k-secret"), patch.object(m, "session", factory):
            self.assertEqual(m.list_models("gpt-image"), ["gpt-image-2.6"])
        self.assertEqual(client.get.call_args.kwargs["headers"]["Authorization"], "Bearer k-secret")

    def test_failures_return_none(self):
        with patch.object(m, "get_key", side_effect=m.UserError("missing")):
            self.assertIsNone(m.list_models("gpt-image"))
        r = MagicMock(status_code=401)
        _, factory = self.fake_session(r)
        with patch.object(m, "get_key", return_value="k"), patch.object(m, "session", factory):
            self.assertIsNone(m.list_models("gpt-image"))
        r = MagicMock(status_code=200)
        r.json.side_effect = ValueError("html")
        _, factory = self.fake_session(r)
        with patch.object(m, "get_key", return_value="k"), patch.object(m, "session", factory):
            self.assertIsNone(m.list_models("gpt-image"))


class SubmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="models 中文 ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prompt = self.root / "p.txt"
        self.prompt.write_text("雨夜城市", encoding="utf-8")
        self.session_file = self.root / "session.json"
        self.available = {"gpt-image": ["gpt-image-2.6"], "nanobanana": ["gemini-3.1-flash-image", "gemini-3.2-flash-image"]}
        for target, value in (("get_key", lambda group="gpt-image": "key-" + group),
                              ("list_models", lambda group, direct=False: self.available[group]),
                              ("has_key", lambda group: True)):
            p = patch.object(m, target, side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(m.os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def args(self, out, **extra):
        values = dict(prompt_file=self.prompt, out_dir=self.root / out, image=[], image_url=[], model=None,
                      quality=None, aspect_ratio=None, image_size=None, upload_provider="tmpfiles", direct=False,
                      session_file=self.session_file, continue_latest=False, from_state=None, strict_model=False)
        values.update(extra)
        return argparse.Namespace(**values)

    def state(self, args):
        return json.loads((args.out_dir / "state.json").read_text(encoding="utf-8"))

    def test_default_gpt_image_uses_upgraded_model(self):
        args = self.args("v1")
        out = io.StringIO()
        with patch.object(m, "api_call", return_value={"id": "task_new", "status": "queued"}) as call, patch("sys.stdout", out):
            m.submit(args)
        self.assertEqual(call.call_args.args[4]["model"], "gpt-image-2.6")
        state = self.state(args)
        self.assertEqual((state["model"], state["model_requested"]), ("gpt-image-2.6", "gpt-image"))
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines[0], {"model": "gpt-image-2.6", "requested": "gpt-image", "resolution": "latest"})
        self.assertEqual(lines[-1]["model"], "gpt-image-2.6")

    def test_old_explicit_name_still_works_after_upgrade(self):
        args = self.args("old-name", model="gpt-image-2.5")
        out = io.StringIO()
        with patch.object(m, "api_call", return_value={"id": "task_new", "status": "queued"}) as call, patch("sys.stdout", out):
            m.submit(args)
        self.assertEqual(call.call_args.args[4]["model"], "gpt-image-2.6")
        self.assertIn('"substituted"', out.getvalue())
        self.assertEqual(self.state(args)["model_requested"], "gpt-image-2.5")

    def test_strict_model_stops_before_any_state_or_request(self):
        args = self.args("strict", model="gpt-image-2.5", strict_model=True)
        with patch.object(m, "api_call", side_effect=AssertionError("no paid request")), self.assertRaises(m.UserError):
            m.submit(args)
        self.assertFalse((args.out_dir / "state.json").exists())

    def test_continue_from_retired_parent_model_upgrades(self):
        parent = self.root / "parent"
        parent.mkdir()
        (parent / "image.png").write_bytes(b"")
        (parent / "state.json").write_text(json.dumps({"version": 2, "phase": "completed", "task_id": "task_old",
                                                        "model": "gpt-image-2.5", "output": "image.png"}))
        args = self.args("child", from_state=parent / "state.json")

        def api(method, endpoint, key, direct=False, payload=None):
            if method == "GET":
                return {"status": "completed", "detail": {"data": [{"download_url": "https://cdn.example.com/a.png"}]}}
            return {"id": "task_child", "status": "queued"}
        out = io.StringIO()
        with patch.object(m, "api_call", side_effect=api) as call, patch("sys.stdout", out):
            m.submit(args)
        post = [c for c in call.call_args_list if c.args[0] == "POST"][0]
        self.assertEqual(post.args[1], "/edits")
        self.assertEqual(post.args[4]["model"], "gpt-image-2.6")
        self.assertIn('"requested": "gpt-image-2.5"', out.getvalue())

    def test_nanobanana_alias_picks_newest_flash(self):
        args = self.args("nb", model="nanobanana")
        r = MagicMock(status_code=200)
        buf = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buf, "PNG")
        r.json.return_value = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png",
                               "data": base64.b64encode(buf.getvalue()).decode()}}]}, "finishReason": "STOP"}]}
        client = MagicMock()
        client.post.return_value = r
        cm = MagicMock()
        cm.__enter__.return_value = client
        cm.__exit__.return_value = False
        with patch.object(m, "session", lambda direct=False: cm), patch("sys.stdout", io.StringIO()):
            m.submit(args)
        self.assertIn("/gemini-3.2-flash-image:generateContent", client.post.call_args.args[0])
        self.assertEqual(self.state(args)["model"], "gemini-3.2-flash-image")

    def test_models_command_reports_latest_per_alias(self):
        r = MagicMock(status_code=200)
        r.json.return_value = {"data": [{"id": "gemini-3.1-flash-image"}, {"id": "gemini-3.2-flash-image"},
                                        {"id": "gemini-3-pro-image-preview"}]}
        client = MagicMock()
        client.get.return_value = r
        cm = MagicMock()
        cm.__enter__.return_value = client
        cm.__exit__.return_value = False
        out = io.StringIO()
        with patch.object(m, "session", lambda direct=False: cm), patch("sys.stdout", out):
            m.models(argparse.Namespace(group="nanobanana", direct=False))
        result = json.loads(out.getvalue())
        self.assertEqual(result["latest"], {"nanobanana": "gemini-3.2-flash-image", "nanobanana-pro": "gemini-3-pro-image-preview"})
        self.assertEqual(result["default_model"], "gemini-3.2-flash-image")
        self.assertTrue(result["default_available"])

    def test_cli_accepts_alias_and_strict_flag(self):
        argv = ["apinebula_image.py", "submit", "--prompt-file", str(self.prompt), "--out-dir", str(self.root / "cli"),
                "--model", "gpt-image", "--strict-model"]
        with patch.object(m.sys, "argv", argv), patch.object(m, "submit") as submit:
            self.assertEqual(m.main(), 0)
        parsed = submit.call_args.args[0]
        self.assertEqual((parsed.model, parsed.strict_model), ("gpt-image", True))


if __name__ == "__main__":
    unittest.main()
