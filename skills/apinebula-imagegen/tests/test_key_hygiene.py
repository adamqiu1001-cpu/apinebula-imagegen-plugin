"""Offline tests: invisible characters in keys and gateway-level HTTP 4xx refusals. No network."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

SPEC = importlib.util.spec_from_file_location("apinebula_image_keys", Path(__file__).parents[1] / "scripts/apinebula_image.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

GOOD = "sk-AbC123xyz_-.789"


def response(status, data=None, json_error=False):
    r = MagicMock(status_code=status)
    if json_error:
        r.json.side_effect = ValueError("empty body")
    else:
        r.json.return_value = data
    return r


def fake_session(get=None, post=None, request=None):
    client = MagicMock()
    for name, value in (("get", get), ("post", post), ("request", request)):
        if isinstance(value, MagicMock):
            getattr(client, name).return_value = value
        elif value is not None:
            getattr(client, name).side_effect = value
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    return client, (lambda direct=False: cm)


class CleanKeyTests(unittest.TestCase):
    def test_bracketed_paste_markers_are_removed(self):
        for raw in ("\x1b[200~" + GOOD + "\x1b[201~", "[200~" + GOOD + "[201~\n", "\ufeff" + GOOD, "  " + GOOD + "\r\n"):
            with self.subTest(raw=repr(raw)):
                key, cleaned = m.clean_key(raw)
                self.assertEqual(key, GOOD)
        self.assertEqual(m.clean_key(GOOD + "\n"), (GOOD, False))
        self.assertTrue(m.clean_key("\x1b[200~" + GOOD + "\x1b[201~")[1])

    def test_other_invisible_or_foreign_characters_are_refused(self):
        for raw in ("sk-abc\x1bdef", "sk-abc\x7f", "sk-ab c", "sk-ab\tc", "sk-ab\u200bc", "sk-密钥", "sk-ab\x00c"):
            with self.subTest(raw=repr(raw)), self.assertRaises(m.UserError) as err:
                m.clean_key(raw, "nanobanana", "file x")
            text = str(err.exception)
            self.assertIn("configure-key --group nanobanana", text)
            self.assertIn("U+", text)
            self.assertNotIn("sk-ab", text)
            self.assertNotIn("sk-密", text)

    def test_empty_after_cleaning_is_refused(self):
        for raw in ("", "   ", "\x1b[200~\x1b[201~"):
            with self.subTest(raw=repr(raw)), self.assertRaises(m.UserError):
                m.clean_key(raw)


class GetKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.groups = {g: dict(v, file=d / (g + ".key")) for g, v in m.GROUPS.items()}
        for target, value in ((m, "GROUPS"),):
            p = patch.object(target, value, self.groups)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(m.os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_file_with_paste_markers_still_works_and_warns(self):
        self.groups["nanobanana"]["file"].write_text("\x1b[200~" + GOOD + "\x1b[201~\n", encoding="utf-8")
        err = io.StringIO()
        with patch("sys.stderr", err):
            self.assertEqual(m.get_key("nanobanana"), GOOD)
        self.assertIn("paste markers", err.getvalue())
        self.assertNotIn(GOOD, err.getvalue())

    def test_bad_env_var_is_named_without_showing_key(self):
        with patch.dict(m.os.environ, {"APINEBULA_API_KEY": "sk-se\u200bcret"}):
            with self.assertRaises(m.UserError) as err:
                m.get_key("gpt-image")
        self.assertIn("APINEBULA_API_KEY", str(err.exception))
        self.assertNotIn("secret", str(err.exception))

    def test_missing_key(self):
        with self.assertRaises(m.UserError) as err:
            m.get_key("gpt-image")
        self.assertIn("missing", str(err.exception))

    def test_configure_key_strips_markers_and_refuses_bad_input(self):
        with patch.object(m.getpass, "getpass", return_value="\x1b[200~" + GOOD + "\x1b[201~"), patch("sys.stdout", io.StringIO()) as out:
            m.configure_key("nanobanana")
        self.assertEqual(self.groups["nanobanana"]["file"].read_text(encoding="utf-8"), GOOD + "\n")
        result = json.loads(out.getvalue())
        self.assertEqual(result["length"], len(GOOD))
        self.assertIn("removed", result)
        self.assertNotIn(GOOD, out.getvalue())
        with patch.object(m.getpass, "getpass", return_value="sk-ab\x7fc"), self.assertRaises(m.UserError):
            m.configure_key("gpt-image")
        self.assertFalse(self.groups["gpt-image"]["file"].exists())

    def test_configure_key_warns_when_env_overrides(self):
        with patch.dict(m.os.environ, {"APINEBULA_API_KEY": GOOD}), \
                patch.object(m.getpass, "getpass", return_value=GOOD), patch("sys.stdout", io.StringIO()) as out:
            m.configure_key("gpt-image")
        self.assertIn("overrides", json.loads(out.getvalue())["warning"])


class GatewayRefusalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gw ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prompt = self.root / "p.txt"
        self.prompt.write_text("城市夜景", encoding="utf-8")
        for name, value in (("get_key", lambda group="gpt-image": "key-" + group), ("has_key", lambda group: True)):
            p = patch.object(m, name, side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(m.os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def args(self, out, model):
        return argparse.Namespace(prompt_file=self.prompt, out_dir=self.root / out, image=[], image_url=[], model=model,
                                  quality=None, aspect_ratio=None, image_size=None, upload_provider="tmpfiles",
                                  direct=False, session_file=self.root / "s.json", continue_latest=False,
                                  from_state=None, strict_model=False)

    def state(self, args):
        return json.loads((args.out_dir / "state.json").read_text(encoding="utf-8"))

    def test_empty_400_on_model_list_stops_before_paid_request(self):
        client, factory = fake_session(get=response(400, json_error=True),
                                       request=AssertionError("no paid request"), post=AssertionError("no paid request"))
        args = self.args("stop", "gpt-image")
        with patch.object(m, "session", factory), self.assertRaises(m.UserError) as err:
            m.submit(args)
        self.assertIn("gateway", str(err.exception))
        self.assertIn("invisible characters", str(err.exception))
        client.request.assert_not_called()
        client.post.assert_not_called()
        self.assertFalse((args.out_dir / "state.json").exists())

    def test_gpt_image_empty_400_on_submit_is_failed_not_unknown(self):
        with patch.object(m, "list_models", return_value=["gpt-image-2.5"]):
            client, factory = fake_session(request=response(400, json_error=True))
            args = self.args("gpt", "gpt-image")
            with patch.object(m, "session", factory), patch("sys.stdout", io.StringIO()), self.assertRaises(m.UserError) as err:
                m.submit(args)
        self.assertEqual(client.request.call_count, 1)
        self.assertIn("No task was created", str(err.exception))
        state = self.state(args)
        self.assertEqual(state["phase"], "failed")
        self.assertNotIn("key-", json.dumps(state))
        out = io.StringIO()
        with patch.object(m, "api_call", side_effect=AssertionError("no network")), patch("sys.stdout", out):
            m.status(argparse.Namespace(state=args.out_dir / "state.json", direct=False, wait=0, interval=10))
        self.assertEqual(json.loads(out.getvalue())["status"], "failed")

    def test_gpt_image_json_400_is_failed(self):
        with patch.object(m, "list_models", return_value=["gpt-image-2.5"]):
            client, factory = fake_session(request=response(400, {"error": {"code": "model_not_found"}}))
            args = self.args("gpt-json", "gpt-image")
            with patch.object(m, "session", factory), patch("sys.stdout", io.StringIO()), self.assertRaises(m.UserError) as err:
                m.submit(args)
        self.assertIn("model_not_found", str(err.exception))
        self.assertEqual(self.state(args)["phase"], "failed")

    def test_5xx_and_408_stay_unknown(self):
        for status in (502, 408):
            with self.subTest(status=status), patch.object(m, "list_models", return_value=["gpt-image-2.5"]):
                client, factory = fake_session(request=response(status, json_error=True))
                args = self.args(f"gpt-{status}", "gpt-image")
                with patch.object(m, "session", factory), patch("sys.stdout", io.StringIO()), self.assertRaises(m.UserError) as err:
                    m.submit(args)
                self.assertNotIsInstance(err.exception, m.RejectedError)
                self.assertEqual(self.state(args)["phase"], "submission_outcome_unknown")

    def test_nanobanana_empty_400_is_failed_with_gateway_hint(self):
        with patch.object(m, "list_models", return_value=["gemini-3.1-flash-image"]):
            client, factory = fake_session(post=response(400, json_error=True))
            args = self.args("nb", "nanobanana")
            with patch.object(m, "session", factory), patch("sys.stdout", io.StringIO()), self.assertRaises(m.UserError) as err:
                m.submit(args)
        self.assertIn("gateway", str(err.exception))
        self.assertEqual(client.post.call_count, 1)
        state = self.state(args)
        self.assertEqual((state["phase"], state["error"]), ("failed", "HTTP 400: gateway_rejected"))

    def test_models_command_explains_gateway_and_401(self):
        _, factory = fake_session(get=response(400, json_error=True))
        with patch.object(m, "session", factory), self.assertRaises(m.UserError) as err:
            m.models(argparse.Namespace(group="nanobanana", direct=False))
        self.assertIn("invisible characters", str(err.exception))
        _, factory = fake_session(get=response(401, {"error": {"code": "", "message": "无效的令牌"}}))
        with patch.object(m, "session", factory), self.assertRaises(m.UserError) as err:
            m.models(argparse.Namespace(group="nanobanana", direct=False))
        self.assertIn("invalid or revoked", str(err.exception))


if __name__ == "__main__":
    unittest.main()
