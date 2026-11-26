"""Offline tests for the nanobanana (Gemini) backend and model routing. No network, no real keys."""
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
from requests import Response
from requests.exceptions import ConnectionError, ConnectTimeout, ProxyError, ReadTimeout, SSLError
from urllib3.exceptions import ReadTimeoutError

SPEC = importlib.util.spec_from_file_location("apinebula_image_nb", Path(__file__).parents[1] / "scripts/apinebula_image.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

SECRET = "nb-test-key-never-log"


def png_bytes(color="red", size=(12, 8), fmt="PNG", **options):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt, **options)
    return buf.getvalue()


def gemini_ok(data=None, mime="image/png"):
    raw = data or png_bytes()
    return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime, "data": base64.b64encode(raw).decode()}}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1290, "totalTokenCount": 1295, "extra": "x"}}


def response(status=200, data=None, json_error=False):
    r = MagicMock()
    r.status_code = status
    if json_error:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = data
    return r


class FakeSession:
    def __init__(self, post=None, get=None):
        self.client = MagicMock()
        if isinstance(post, MagicMock):
            self.client.post.return_value = post
        elif post is not None:
            self.client.post.side_effect = post  # exception or callable
        if get is not None:
            self.client.get.return_value = get

    def __call__(self, direct=False):
        cm = MagicMock()
        cm.__enter__.return_value = self.client
        cm.__exit__.return_value = False
        return cm


class NanoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nano 中文 空格 ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prompt = self.root / "提示词.txt"
        self.prompt.write_text("赛博朋克城市夜景", encoding="utf-8")
        self.session_file = self.root / "session.json"
        self.args = self.make_args("出图")
        key = patch.object(m, "get_key", side_effect=lambda group="gpt-image": SECRET if group == "nanobanana" else "gpt-key")
        key.start()
        self.addCleanup(key.stop)
        self.available = {"nanobanana": ["gemini-2.5-flash-image", "gemini-3-pro-image-preview",
                                         "gemini-3.1-flash-image", "gemini-3.1-flash-image-preview"],
                          "gpt-image": ["gpt-image-2.5"]}
        models = patch.object(m, "list_models", side_effect=lambda group, direct=False: self.available[group])
        models.start()
        self.addCleanup(models.stop)

    def make_args(self, out, **extra):
        values = dict(prompt_file=self.prompt, out_dir=self.root / out, image=[], image_url=[],
                      model="gemini-3.1-flash-image", quality=None, aspect_ratio=None, image_size=None,
                      upload_provider="tmpfiles", direct=False, session_file=self.session_file,
                      continue_latest=False, from_state=None)
        values.update(extra)
        return argparse.Namespace(**values)

    def run_submit(self, args, post=None):
        fake = FakeSession(post=post if post is not None else response(200, gemini_ok()))
        with patch.object(m, "session", fake), \
                patch.object(m, "api_call", side_effect=AssertionError("async API must not be used")), \
                patch.object(m, "upload_tmpfiles", side_effect=AssertionError("no third-party upload")):
            m.submit(args)
        return fake.client

    def state(self, args):
        return json.loads((args.out_dir / "state.json").read_text(encoding="utf-8"))


class RoutingTests(NanoCase):
    def test_model_routes_to_group(self):
        self.assertEqual(m.group_for("gemini-3.1-flash-image"), "nanobanana")
        self.assertEqual(m.group_for("gemini-3-pro-image-preview"), "nanobanana")
        self.assertEqual(m.group_for("gpt-image-2.5"), "gpt-image")

    def test_default_request_follows_configured_keys(self):
        with patch.dict(m.os.environ, {}, clear=True):
            with patch.object(m, "has_key", side_effect=lambda g: g == "nanobanana"):
                self.assertEqual(m.requested_model(None), "nanobanana")
            with patch.object(m, "has_key", return_value=True):
                self.assertEqual(m.requested_model(None), "gpt-image")
            with patch.object(m, "has_key", return_value=False):
                self.assertEqual(m.requested_model(None), "gpt-image")
        with patch.dict(m.os.environ, {"APINEBULA_DEFAULT_MODEL": "gemini-3-pro-image-preview"}, clear=True):
            self.assertEqual(m.requested_model(None), "gemini-3-pro-image-preview")
        with patch.dict(m.os.environ, {"APINEBULA_DEFAULT_MODEL": "Gemini-Pro"}, clear=True):
            self.assertEqual(m.requested_model(None), "nanobanana-pro")
        self.assertEqual(m.requested_model("gpt-image-2.5"), "gpt-image-2.5")
        for bad in ("../x", "gemini 3", "a/b", "gemini:generate"):
            with self.subTest(bad=bad), self.assertRaises(m.UserError):
                m.requested_model(bad)

    def test_group_keys_are_separate(self):
        with tempfile.TemporaryDirectory() as d:
            groups = {g: dict(v, file=Path(d) / (g + ".key")) for g, v in m.GROUPS.items()}
            with patch.object(m, "GROUPS", groups), patch.dict(m.os.environ, {}, clear=True), \
                    patch.object(m.getpass, "getpass", return_value="sk-nano"):
                m.configure_key("nanobanana")
                self.assertTrue((Path(d) / "nanobanana.key").exists())
                self.assertFalse((Path(d) / "gpt-image.key").exists())
                self.assertEqual((Path(d) / "nanobanana.key").read_text().strip(), "sk-nano")
                self.assertTrue(m.has_key("nanobanana"))
                self.assertFalse(m.has_key("gpt-image"))

    def test_claude_code_session_id_scopes_chat(self):
        with patch.dict(m.os.environ, {"CLAUDE_CODE_SESSION_ID": "claude-a"}, clear=True):
            a = m.session_path()
        with patch.dict(m.os.environ, {"CLAUDE_CODE_SESSION_ID": "claude-b"}, clear=True):
            b = m.session_path()
        with patch.dict(m.os.environ, {"CODEX_THREAD_ID": "t", "CLAUDE_CODE_SESSION_ID": "claude-a"}, clear=True):
            c = m.session_path()
        self.assertIsNotNone(a)
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_gpt_only_options_rejected_for_gemini_and_vice_versa(self):
        with self.assertRaises(m.UserError):
            self.run_submit(self.make_args("q", quality="high"))
        with self.assertRaises(m.UserError):
            self.run_submit(self.make_args("s3", upload_provider="s3"))
        gpt = self.make_args("gpt", model="gpt-image-2.5", aspect_ratio="16:9")
        with patch.object(m, "api_call", side_effect=AssertionError("no network")), self.assertRaises(m.UserError):
            m.submit(gpt)
        self.assertFalse((self.root / "q" / "state.json").exists())


class GenerationTests(NanoCase):
    def test_text_generation_payload_and_result(self):
        args = self.make_args("出图", aspect_ratio="16:9", image_size="2K")
        client = self.run_submit(args)
        client.post.assert_called_once()
        url = client.post.call_args.args[0]
        self.assertEqual(url, "https://img-api.apinebula.ai/v1beta/models/gemini-3.1-flash-image:generateContent")
        kwargs = client.post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + SECRET)
        self.assertFalse(kwargs["allow_redirects"])
        payload = kwargs["json"]
        self.assertEqual(payload["contents"][0]["parts"], [{"text": "赛博朋克城市夜景"}])
        self.assertEqual(payload["generationConfig"]["imageConfig"], {"aspectRatio": "16:9", "imageSize": "2K"})
        self.assertEqual(payload["generationConfig"]["responseModalities"], ["IMAGE"])
        state = self.state(args)
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["provider"], "nanobanana")
        self.assertEqual(state["output"], "image.png")
        self.assertEqual(state["size"], [12, 8])
        self.assertEqual(state["usage"], {"promptTokenCount": 5, "candidatesTokenCount": 1290, "totalTokenCount": 1295})
        raw = (args.out_dir / "state.json").read_text(encoding="utf-8")
        self.assertNotIn(SECRET, raw)
        self.assertNotIn("data", json.loads(raw).get("image_config", {}))
        self.assertTrue((args.out_dir / "image.png").exists())
        self.assertFalse((args.out_dir / "download.part").exists())
        latest = json.loads(self.session_file.read_text(encoding="utf-8"))["latest_state"]
        self.assertEqual(Path(latest), (args.out_dir / "state.json").resolve())

    def test_default_request_has_no_image_config(self):
        args = self.make_args("plain")
        client = self.run_submit(args)
        self.assertNotIn("imageConfig", client.post.call_args.kwargs["json"]["generationConfig"])

    def test_jpeg_result_keeps_real_extension(self):
        args = self.make_args("jpeg")
        self.run_submit(args, post=response(200, gemini_ok(png_bytes(fmt="JPEG"), "image/jpeg")))
        self.assertEqual(self.state(args)["output"], "image.jpg")

    def test_duplicate_directory_stops_before_network(self):
        args = self.make_args("dup")
        self.run_submit(args)
        fake = FakeSession(post=AssertionError("must not post twice"))
        with patch.object(m, "session", fake), self.assertRaises(FileExistsError):
            m.submit(self.make_args("dup"))
        fake.client.post.assert_not_called()
        self.assertEqual(self.state(args)["phase"], "completed")

    def test_local_reference_is_inlined_without_upload(self):
        image = self.root / "本地 参考.png"
        image.write_bytes(png_bytes("blue"))
        args = self.make_args("edit", image=[image])
        client = self.run_submit(args)
        parts = client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), image.read_bytes())
        self.assertEqual(self.state(args)["uploads"], [])

    def assert_smaller_jpeg(self, inline, original, size):
        sent = base64.b64decode(inline["data"])
        self.assertEqual(inline["mimeType"], "image/jpeg")
        self.assertLess(len(sent), len(original))
        with Image.open(io.BytesIO(sent)) as decoded:
            self.assertEqual((decoded.format, decoded.size), ("JPEG", size))
            decoded.load()

    def test_local_opaque_png_is_inlined_as_smaller_jpeg_without_changing_source(self):
        image = self.root / "本地 大图.png"
        size = (512, 256)
        original = png_bytes("steelblue", size, compress_level=0)
        image.write_bytes(original)
        args = self.make_args("smaller", image=[image])
        client = self.run_submit(args)
        parts = client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assert_smaller_jpeg(parts[1]["inlineData"], original, size)
        self.assertEqual(image.read_bytes(), original)
        self.assertTrue((args.out_dir / "image.png").is_file())
        client.post.assert_called_once()

    def test_local_references_with_transparency_animation_or_color_profile_stay_unchanged(self):
        size = (64, 64)
        palette = Image.new("P", size)
        palette.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        fixtures = (
            ("alpha", Image.new("RGBA", size, (10, 20, 30, 64)), {}),
            ("color-key", Image.new("RGB", size, (1, 2, 3)), {"transparency": (1, 2, 3)}),
            ("palette", palette, {"transparency": 0}),
            ("animation", Image.new("RGB", size, "red"),
             {"save_all": True, "append_images": [Image.new("RGB", size, "blue")]}),
            ("profile", Image.new("RGB", size, "green"), {"icc_profile": b"test profile"}),
        )
        paths = []
        for name, image, options in fixtures:
            path = self.root / (name + ".png")
            image.save(path, compress_level=0, **options)
            paths.append(path)
        jpeg = self.root / "existing.jpg"
        jpeg.write_bytes(png_bytes("purple", size, fmt="JPEG"))
        client = self.run_submit(self.make_args("unchanged", image=[*paths, jpeg]))
        parts = client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        for path, part in zip([*paths, jpeg], parts[1:], strict=True):
            with self.subTest(path=path.name):
                self.assertEqual(part["inlineData"]["mimeType"], "image/jpeg" if path == jpeg else "image/png")
                self.assertEqual(base64.b64decode(part["inlineData"]["data"]), path.read_bytes())

    def test_keep_reference_format_sends_original_local_png(self):
        image = self.root / "original.png"
        original = png_bytes("gold", (128, 128), compress_level=0)
        image.write_bytes(original)
        client = self.run_submit(self.make_args("lossless", image=[image], keep_reference_format=True))
        parts = client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), original)

    def test_jpeg_encoder_failure_sends_original_local_png(self):
        image = self.root / "fallback.png"
        original = png_bytes("blue", (128, 128), compress_level=0)
        image.write_bytes(original)
        with patch.object(Image.Image, "save", side_effect=OSError("JPEG encoder unavailable")):
            client = self.run_submit(self.make_args("fallback", image=[image]),
                                     post=response(200, gemini_ok(original)))
        parts = client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), original)

    def test_online_reference_fetched_without_credentials(self):
        ref = MagicMock()
        ref.is_redirect = False
        original = png_bytes("green", (128, 128), compress_level=0)
        ref.iter_content.return_value = [original]
        ref.__enter__.return_value = ref
        ref.__exit__.return_value = False
        args = self.make_args("url", image_url=["https://example.com/ref.png"])
        fake = FakeSession(post=response(200, gemini_ok()), get=ref)
        with patch.object(m, "session", fake), patch.object(m, "api_call", side_effect=AssertionError):
            m.submit(args)
        get_kwargs = fake.client.get.call_args.kwargs
        self.assertNotIn("headers", get_kwargs)
        self.assertEqual(fake.client.get.call_args.args[0], "https://example.com/ref.png")
        parts = fake.client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), original)

    def test_local_png_parent_is_optimized_for_both_followup_options(self):
        first = self.make_args("png-parent")
        size = (512, 256)
        original = png_bytes("white", size, compress_level=0)
        self.run_submit(first, post=response(200, gemini_ok(original)))
        explicit = self.make_args("from-parent", from_state=first.out_dir / "state.json")
        latest = self.make_args("continue-parent", continue_latest=True)
        for args in (latest, explicit):
            with self.subTest(args=args.out_dir.name):
                client = self.run_submit(args)
                parts = client.post.call_args.kwargs["json"]["contents"][0]["parts"]
                self.assert_smaller_jpeg(parts[1]["inlineData"], original, size)
        self.assertEqual((first.out_dir / "image.png").read_bytes(), original)

    def test_continue_from_nanobanana_parent_inlines_local_image(self):
        first = self.make_args("v1")
        self.run_submit(first, post=response(200, gemini_ok(png_bytes("purple"))))
        second = self.make_args("v2", continue_latest=True)
        client = self.run_submit(second)
        parts = client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), (first.out_dir / "image.png").read_bytes())
        self.assertEqual(Path(self.state(second)["parent_state"]), (first.out_dir / "state.json").resolve())
        latest = json.loads(self.session_file.read_text(encoding="utf-8"))["latest_state"]
        self.assertEqual(Path(latest), (second.out_dir / "state.json").resolve())

    def test_continue_without_model_keeps_parent_model(self):
        first = self.make_args("pro-v1", model="gemini-3-pro-image-preview")
        self.run_submit(first)
        second = self.make_args("pro-v2", model=None, continue_latest=True)
        with patch.dict(m.os.environ, {}, clear=True), patch.object(m, "has_key", return_value=True):
            client = self.run_submit(second)
        self.assertIn("gemini-3-pro-image-preview:generateContent", client.post.call_args.args[0])
        self.assertEqual(self.state(second)["model"], "gemini-3-pro-image-preview")

    def test_continue_from_gpt_image_parent_uses_downloaded_copy(self):
        parent = self.root / "gpt-v1"
        parent.mkdir()
        (parent / "image.png").write_bytes(png_bytes("yellow"))
        (parent / "state.json").write_text(json.dumps({"version": 2, "phase": "completed", "task_id": "task_abc",
                                                        "output": "image.png", "completed_at": "2026-10-29T00:00:00+00:00"}))
        args = self.make_args("nb-from-gpt", from_state=parent / "state.json")
        client = self.run_submit(args)
        parts = client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), (parent / "image.png").read_bytes())

    def test_gpt_image_cannot_silently_continue_from_nanobanana_parent(self):
        first = self.make_args("nb1")
        self.run_submit(first)
        gpt = self.make_args("gpt2", model="gpt-image-2.5", continue_latest=True, quality="high")
        with patch.object(m, "api_call", side_effect=AssertionError("no paid request")), \
                patch.object(m, "upload_tmpfiles", side_effect=AssertionError("no silent upload")):
            with self.assertRaises(m.UserError) as err:
                m.submit(gpt)
        self.assertIn("--image", str(err.exception))
        self.assertIn("image.png", str(err.exception))
        self.assertFalse((gpt.out_dir / "state.json").exists())

    def test_missing_parent_file_stops_before_request(self):
        first = self.make_args("gone")
        self.run_submit(first)
        (first.out_dir / "image.png").unlink()
        second = self.make_args("after-gone", continue_latest=True)
        fake = FakeSession(post=AssertionError("must not post"))
        with patch.object(m, "session", fake), self.assertRaises(m.UserError):
            m.submit(second)
        self.assertFalse((second.out_dir / "state.json").exists())


class FailureTests(NanoCase):
    def test_http_error_is_final_and_body_not_echoed(self):
        args = self.make_args("bad")
        body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "prompt 赛博朋克 and secret url https://x/y?sig=1"}}
        with self.assertRaises(m.UserError) as err:
            client = self.run_submit(args, post=response(400, body))
        text = str(err.exception)
        self.assertIn("INVALID_ARGUMENT", text)
        self.assertIn("models --group nanobanana", text)
        self.assertNotIn("sig=1", text)
        state = self.state(args)
        self.assertEqual(state["phase"], "failed")
        self.assertNotIn("sig=1", json.dumps(state))

    def test_auth_error_points_to_key_group(self):
        args = self.make_args("auth")
        with self.assertRaises(m.UserError) as err:
            self.run_submit(args, post=response(401, {"error": {"status": "UNAUTHENTICATED"}}))
        self.assertIn("nanobanana group", str(err.exception))

    def test_non_json_error_keeps_outcome_unknown(self):
        args = self.make_args("gateway")
        with self.assertRaises(m.UserError):
            self.run_submit(args, post=response(502, json_error=True))
        self.assertEqual(self.state(args)["phase"], "submission_outcome_unknown")

    def test_json_gateway_errors_keep_outcome_unknown(self):
        for code in (408, 502, 524):
            with self.subTest(code=code):
                args = self.make_args(f"gateway-{code}")
                fake = FakeSession(post=response(code, {"error": {"status": "PROVIDER_BODY"}}))
                out = io.StringIO()
                with patch.object(m, "session", fake), patch("sys.stdout", out), self.assertRaises(m.UserError) as err:
                    m.submit(args)
                fake.client.post.assert_called_once()
                state = self.state(args)
                self.assertEqual(state["phase"], "submission_outcome_unknown")
                self.assertEqual(state["diagnostic"]["http_status"], code)
                self.assertIn("outcome unknown", str(err.exception))
                fake.client.post.return_value.json.assert_not_called()
                self.assertNotIn("PROVIDER_BODY", str(err.exception) + json.dumps(state) + out.getvalue())

    def run_interrupted_edit(self, name, fake, expected_reason, expected_stage, http_status=None):
        ref = self.root / "参考图.png"
        ref.write_bytes(png_bytes())
        args = self.make_args(name)
        argv = ["submit", "--prompt-file", str(self.prompt), "--out-dir", str(args.out_dir),
                "--model", "nanobanana", "--image", str(ref), "--session-file", str(self.session_file)]
        out = io.StringIO()
        with patch.object(m, "session", fake), patch("sys.stdout", out):
            self.assertEqual(m.main_with_args(argv), 1)
        fake.client.post.assert_called_once()
        state = self.state(args)
        self.assertEqual(state["phase"], "submission_outcome_unknown")
        result = json.loads(out.getvalue().splitlines()[-1])
        self.assertIn("no task ID", result["error"])
        self.assertIn("usage", result["error"])
        diagnostic = state["diagnostic"]
        self.assertEqual(diagnostic["reason"], expected_reason)
        self.assertEqual(diagnostic["stage"], expected_stage)
        self.assertEqual(diagnostic.get("http_status"), http_status)
        self.assertGreaterEqual(diagnostic["elapsed_seconds"], 0)
        self.assertIsNotNone(m.datetime.fromisoformat(state["submission_started_at"]).utcoffset())
        self.assertEqual(result["diagnostic"], diagnostic)
        for secret in (SECRET, "sig=secret", "proxy-password"):
            self.assertNotIn(secret, json.dumps(state) + out.getvalue())
        self.assertFalse(list(args.out_dir.glob("image.*")))
        state_args = argparse.Namespace(state=args.out_dir / "state.json", direct=False, wait=0, interval=10)
        with patch.object(m, "session", side_effect=AssertionError("no network")):
            with self.assertRaises(m.UserError):
                m.status(state_args)
            with self.assertRaises(m.UserError):
                m.resume(state_args)
        self.assertFalse((args.out_dir / "resume.lock").exists())

    def test_transport_setup_errors_never_trigger_a_retry(self):
        for exception, reason in ((ConnectTimeout, "connect_timeout"), (SSLError, "tls_error")):
            with self.subTest(reason=reason):
                self.run_interrupted_edit(reason, FakeSession(post=exception("sig=secret")),
                                          reason, "waiting_for_headers")

    def test_interrupted_edit_distinguishes_proxy_timeout_and_generic_connection(self):
        for exception, reason in ((ProxyError, "proxy_error"), (ReadTimeout, "read_timeout"),
                                  (ConnectionError, "connection_error")):
            with self.subTest(reason=reason):
                fake = FakeSession(post=exception(f"https://proxy-password@example.com/?sig=secret&key={SECRET}"))
                self.run_interrupted_edit(reason, fake, reason, "waiting_for_headers")

    def test_read_timeout_in_partial_image_response_is_not_reported_as_connection_setup(self):
        # Use Requests' actual response reader: it wraps urllib3 read timeouts in ConnectionError.
        class PartialBody:
            def stream(self, *args, **kwargs):
                yield b'{"candidates": ['
                raise ReadTimeoutError(None, "https://example.com/?sig=secret", SECRET)

        result = Response()
        result.status_code = 200
        result.raw = PartialBody()
        fake = FakeSession()
        fake.client.post.return_value = result
        with patch.object(result, "close") as close:
            self.run_interrupted_edit("partial", fake, "read_timeout", "reading_body", 200)
        self.assertTrue(fake.client.post.call_args.kwargs["stream"])
        close.assert_called_once()

    def test_http_success_without_valid_json_has_unknown_outcome(self):
        self.run_interrupted_edit("invalid-json", FakeSession(post=response(200, json_error=True)),
                                  "invalid_response", "reading_body", 200)

    def test_answer_without_image_is_failed_not_retried(self):
        args = self.make_args("blocked")
        data = {"candidates": [{"content": {"parts": [{"text": "I can't help with that."}]}, "finishReason": "IMAGE_SAFETY"}]}
        out = io.StringIO()
        with patch("sys.stdout", out):
            client = self.run_submit(args, post=response(200, data))
        client.post.assert_called_once()
        state = self.state(args)
        self.assertEqual(state["phase"], "failed")
        self.assertEqual(state["finish_reason"], "IMAGE_SAFETY")
        self.assertNotIn("I can't help", out.getvalue())

    def test_corrupt_image_data_is_failed(self):
        args = self.make_args("corrupt")
        data = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": base64.b64encode(b"not an image").decode()}}]}}]}
        with self.assertRaises(m.UserError):
            self.run_submit(args, post=response(200, data))
        self.assertEqual(self.state(args)["phase"], "failed")
        self.assertFalse(list(args.out_dir.glob("image.*")))

    def test_status_reads_completed_result_without_network_or_key(self):
        args = self.make_args("done")
        self.run_submit(args)
        state_args = argparse.Namespace(state=args.out_dir / "state.json", direct=False, wait=0, interval=10)
        out = io.StringIO()
        with patch.object(m, "session", side_effect=AssertionError("no network")), \
                patch.object(m, "get_key", side_effect=AssertionError("no key")), patch("sys.stdout", out):
            m.status(state_args)
        self.assertIn('"completed"', out.getvalue())


class ModelsCommandTests(NanoCase):
    def test_models_lists_ids_with_group_key(self):
        data = {"data": [{"id": "gemini-3.1-flash-image"}, {"id": "gemini-3-pro-image-preview"}, {"bad": 1}]}
        fake = FakeSession(get=response(200, data))
        out = io.StringIO()
        with patch.object(m, "session", fake), patch("sys.stdout", out):
            m.models(argparse.Namespace(group="nanobanana", direct=False))
        result = json.loads(out.getvalue())
        self.assertEqual(result["models"], ["gemini-3-pro-image-preview", "gemini-3.1-flash-image"])
        self.assertTrue(result["default_available"])
        self.assertEqual(fake.client.get.call_args.kwargs["headers"]["Authorization"], "Bearer " + SECRET)
        self.assertNotIn(SECRET, out.getvalue())

    def test_cli_parses_new_options(self):
        argv = ["apinebula_image.py", "submit", "--prompt-file", str(self.prompt), "--out-dir", str(self.root / "cli"),
                "--model", "gemini-3.1-flash-image", "--aspect-ratio", "9:16", "--image-size", "4K",
                "--keep-reference-format"]
        with patch.object(m.sys, "argv", argv), patch.object(m, "submit") as submit:
            self.assertEqual(m.main(), 0)
        parsed = submit.call_args.args[0]
        self.assertEqual((parsed.aspect_ratio, parsed.image_size, parsed.quality), ("9:16", "4K", None))
        self.assertTrue(parsed.keep_reference_format)
        with patch.object(m.sys, "argv", ["apinebula_image.py", "configure-key", "--group", "nanobanana"]), \
                patch.object(m, "configure_key") as configure:
            self.assertEqual(m.main(), 0)
        configure.assert_called_once_with("nanobanana")


if __name__ == "__main__":
    unittest.main()
