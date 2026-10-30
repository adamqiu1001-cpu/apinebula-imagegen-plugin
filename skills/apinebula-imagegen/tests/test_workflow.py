"""Offline tests: no paid generation, private storage or real keys required."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from PIL import Image

SPEC = importlib.util.spec_from_file_location("apinebula_image", Path(__file__).parents[1] / "scripts/apinebula_image.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nebula 中文 空格 ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prompt = self.root / "提示词.txt"
        self.prompt.write_text("雨夜城市", encoding="utf-8")
        self.args = argparse.Namespace(prompt_file=self.prompt, out_dir=self.root / "输出", image=[],
                                       image_url=[], model="gpt-image-2.5", quality="high", direct=False, session_file=self.root / "session.json")
        self.key = patch.object(m, "get_key", return_value="test-key-never-log")
        self.key.start()
        self.addCleanup(self.key.stop)

    def create(self, result=None):
        with patch.object(m, "api_call", return_value=result or {"id": "task_test", "status": "queued"}) as call:
            m.submit(self.args)
            return call

    def state_args(self):
        return argparse.Namespace(state=self.args.out_dir / "state.json", direct=False)


class WorkflowTests(WorkflowCase):
    def test_text_submission_and_duplicate_guard(self):
        call = self.create()
        self.assertEqual(call.call_args.args[1], "/generations")
        payload = call.call_args.args[4]
        self.assertEqual(payload["prompt"], "雨夜城市")
        self.assertNotIn("images", payload)
        with patch.object(m, "api_call") as again:
            with self.assertRaises(FileExistsError):
                m.submit(self.args)
            again.assert_not_called()

    def test_url_reference_uses_edits(self):
        self.args.image_url = ["https://example.com/reference.png"]
        call = self.create()
        self.assertEqual(call.call_args.args[1], "/edits")
        self.assertEqual(call.call_args.args[4]["images"][0]["image_url"], self.args.image_url[0])

    def test_local_reference_upload_and_sign_before_submit(self):
        image = self.root / "本地 图.png"
        Image.new("RGB", (16, 16), "blue").save(image)
        self.args.image = [image]
        self.args.upload_provider = "s3"
        cfg = {"endpoint": "https://storage.example.com", "bucket": "private"}
        client = MagicMock()
        client.generate_presigned_url.return_value = "https://storage.example.com/ref.png?signature=secret"
        with patch.object(m, "storage", return_value=cfg), patch.object(m, "storage_client", return_value=client):
            call = self.create()
        client.put_object.assert_called_once()
        self.assertEqual(client.generate_presigned_url.call_args.kwargs["ExpiresIn"], 86400)
        self.assertEqual(call.call_args.args[1], "/edits")
        self.assertIn("signature=secret", call.call_args.args[4]["images"][0]["image_url"])
        state = self.state_args().state.read_text()
        self.assertNotIn("signature", state)
        self.assertNotIn("test-key", state)

    def test_missing_storage_stops_before_submission(self):
        image = self.root / "ref.png"
        Image.new("RGB", (8, 8)).save(image)
        self.args.image = [image]
        self.args.upload_provider = "s3"
        with patch.object(m, "STORAGE_FILE", self.root / "missing.json"), patch.object(m, "api_call") as call:
            with self.assertRaises(m.UserError):
                m.submit(self.args)
            call.assert_not_called()
        self.assertFalse(self.args.out_dir.exists())

    def test_uncertain_submission_is_retained_without_retry(self):
        with patch.object(m, "api_call", side_effect=ConnectionError("test")) as call:
            with self.assertRaises(ConnectionError):
                m.submit(self.args)
            self.assertEqual(call.call_count, 1)
        state = json.loads(self.state_args().state.read_text())
        self.assertEqual(state["phase"], "submission_outcome_unknown")
        with patch.object(m, "api_call") as call:
            with self.assertRaises(m.UserError):
                m.status(self.state_args())
            call.assert_not_called()

    def test_status_recovers_and_downloads_once(self):
        self.create()
        result = {"status": "completed", "detail": {"data": [{"download_url": "https://cdn.example.com/out.png"}]}}
        output = self.args.out_dir / "image.png"

        def downloaded(*args):
            Image.new("RGB", (20, 20)).save(output)
            return output, (20, 20)

        with patch.object(m, "api_call", return_value=result) as call, patch.object(m, "download", side_effect=downloaded) as dl:
            m.status(self.state_args())
            m.status(self.state_args())
            self.assertTrue(all(c.args[0] == "GET" for c in call.call_args_list))
            self.assertEqual(dl.call_count, 1)

    def test_failed_task_does_not_resubmit(self):
        self.create()
        with patch.object(m, "api_call", return_value={"status": "failed"}) as call, patch.object(m, "download") as dl:
            m.status(self.state_args())
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.args[0], "GET")
            dl.assert_not_called()

    def test_download_retry_reuses_task(self):
        self.create()
        result = {"status": "completed", "detail": {"data": [{"download_url": "https://cdn.example.com/out.png"}]}}
        with patch.object(m, "api_call", return_value=result), patch.object(m, "download", side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                m.status(self.state_args())
        self.assertEqual(json.loads(self.state_args().state.read_text())["task_id"], "task_test")

    def test_invalid_reference_urls_rejected(self):
        for url in ("data:image/png;base64,eA==", "file:///tmp/a.png", "http://example.com/a.png", "https://user:secret@example.com/a"):
            with self.subTest(url=url), self.assertRaises(m.UserError):
                m.https_url(url)

    def test_download_validates_real_image_and_does_not_send_auth(self):
        b = io.BytesIO()
        Image.new("RGB", (12, 15)).save(b, format="PNG")
        r = MagicMock()
        r.is_redirect = False
        r.iter_content.return_value = [b.getvalue()]
        s = MagicMock()
        s.get.return_value = r
        with patch.object(m, "session") as factory:
            factory.return_value.__enter__.return_value = s
            output, size = m.download("https://cdn.example.com/image", self.root)
        self.assertEqual(size, (12, 15))
        self.assertEqual(output.suffix, ".png")
        self.assertNotIn("headers", s.get.call_args.kwargs)

    def test_corrupt_download_not_promoted_to_final(self):
        r = MagicMock()
        r.is_redirect = False
        r.iter_content.return_value = [b"not an image"]
        with patch.object(m, "session") as factory:
            factory.return_value.__enter__.return_value.get.return_value = r
            with self.assertRaises(Exception):
                m.download("https://cdn.example.com/image", self.root)
        self.assertFalse((self.root / "image.png").exists())

    @unittest.skipIf(m.IS_WINDOWS, "POSIX mode bits do not represent Windows ACLs")
    def test_private_file_posix_mode(self):
        path = self.root / "key"
        with patch.object(m, "IS_WINDOWS", False):
            m.private_file(path, "test-secret")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_windows_acl_before_secret_write(self):
        path = self.root / "key"

        def acl(args, **kwargs):
            self.assertEqual(Path(args[1]).read_bytes(), b"")
            self.assertIn("/inheritance:r", args)
            self.assertIn("DOMAIN\\tester:(F)", args)

        with patch.object(m, "IS_WINDOWS", True), patch.object(m.subprocess, "check_output", return_value="DOMAIN\\tester\n"), patch.object(m.subprocess, "run", side_effect=acl):
            m.private_file(path, "test-secret")
        self.assertEqual(path.read_text(), "test-secret")

    def test_windows_acl_failure_writes_no_secret(self):
        path = self.root / "key"
        with patch.object(m, "IS_WINDOWS", True), patch.object(m.subprocess, "check_output", return_value="tester\n"), patch.object(m.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "icacls")):
            with self.assertRaises(subprocess.CalledProcessError):
                m.private_file(path, "test-secret")
        self.assertFalse(path.exists())
        self.assertFalse(list(self.root.glob(".secret-*")))

    def test_cleanup_restricted_to_recorded_terminal_task(self):
        self.create()
        args = self.state_args()
        with self.assertRaises(m.UserError):
            m.cleanup(args)
        state = json.loads(args.state.read_text())
        state.update(phase="completed", uploads=[{"endpoint": "https://store.example.com", "bucket": "b", "key": "codex-image-reference/" + "a" * 32 + ".png"}])
        m.save(args.state, state)
        client = MagicMock()
        with patch.object(m, "storage", return_value={"endpoint": "https://store.example.com", "bucket": "b"}), patch.object(m, "storage_client", return_value=client):
            m.cleanup(args)
        client.delete_object.assert_called_once()
        self.assertEqual(json.loads(args.state.read_text())["uploads"], [])


class ContinuationTests(WorkflowCase):
    def completed_parent(self):
        self.create()
        output = self.args.out_dir / "image.png"
        Image.new("RGB", (20, 20), "blue").save(output)
        state = json.loads(self.state_args().state.read_text())
        state.update(phase="completed", output="image.png", size=[20, 20])
        m.save(self.state_args().state, state)
        return self.state_args().state

    def test_continue_reuses_parent_url_without_local_upload(self):
        parent = self.completed_parent()
        self.args.from_state = parent
        self.args.out_dir = self.root / "second"
        requests = []
        def api(method, endpoint, key, direct=False, payload=None):
            requests.append((method, endpoint, payload))
            if method == "GET":
                return {"status": "completed", "detail": {"data": [{"download_url": "https://cdn.example.com/fresh.png?token=private"}]}}
            return {"id": "task_second", "status": "queued"}
        with patch.object(m, "api_call", side_effect=api):
            m.submit(self.args)
        self.assertEqual([x[0] for x in requests], ["GET", "POST"])
        self.assertEqual(requests[-1][1], "/edits")
        self.assertEqual(requests[-1][2]["images"], [{"image_url": "https://cdn.example.com/fresh.png?token=private"}])
        state = json.loads((self.args.out_dir / "state.json").read_text())
        self.assertEqual(Path(state["parent_state"]), parent.resolve())
        self.assertNotIn("token=private", json.dumps(state))

    def test_only_downloaded_success_becomes_latest(self):
        self.create()
        sessionfile = self.args.session_file
        self.assertFalse(sessionfile.exists())
        output = self.args.out_dir / "image.png"
        def downloaded(*args):
            Image.new("RGB", (20, 20)).save(output)
            return output, (20, 20)
        response = {"status": "completed", "detail": {"data": [{"download_url": "https://cdn.example.com/out.png"}]}}
        with patch.object(m, "api_call", return_value=response), patch.object(m, "download", side_effect=downloaded):
            m.status(self.state_args())
        selected = json.loads(sessionfile.read_text())
        self.assertEqual(Path(selected["latest_state"]), self.state_args().state.resolve())
        self.args.out_dir = self.root / "failed-later"
        self.create({"id": "task_failed", "status": "queued"})
        with patch.object(m, "api_call", return_value={"status": "failed"}):
            m.status(self.state_args())
        self.assertEqual(json.loads(sessionfile.read_text()), selected)

    def test_continue_uses_session_selection_and_fails_if_missing(self):
        self.args.continue_latest = True
        with self.assertRaises(m.UserError), patch.object(m, "api_call") as api:
            m.submit(self.args)
        api.assert_not_called()
        self.args.continue_latest = False
        parent = self.completed_parent()
        self.args.continue_latest = True
        m.save(self.args.session_file, {"latest_state": str(parent.resolve())})
        self.args.out_dir = self.root / "continued"
        def api(method, endpoint, key, direct=False, payload=None):
            if method == "GET":
                return {"status": "completed", "detail": {"data": [{"download_url": "https://cdn.example.com/current.png"}]}}
            return {"id": "task_continued", "status": "queued"}
        with patch.object(m, "api_call", side_effect=api):
            m.submit(self.args)
        self.assertEqual(json.loads((self.args.out_dir / "state.json").read_text())["parent_state"], str(parent.resolve()))

    def test_chat_sessions_do_not_share_latest(self):
        with patch.dict(m.os.environ, {"CODEX_THREAD_ID": "chat-a"}):
            a = m.session_path()
        with patch.dict(m.os.environ, {"CODEX_THREAD_ID": "chat-b"}):
            b = m.session_path()
        self.assertNotEqual(a, b)
        with patch.dict(m.os.environ, {}, clear=True):
            self.assertIsNone(m.session_path())

    def test_duplicate_continuation_stops_before_network(self):
        parent = self.completed_parent()
        self.args.from_state = parent
        with patch.object(m, "api_call") as api, self.assertRaises(FileExistsError):
            m.submit(self.args)
        api.assert_not_called()

    def test_rechecking_older_success_does_not_replace_newer_selection(self):
        parent = self.completed_parent()
        state = json.loads(parent.read_text())
        state["completed_at"] = "2026-10-27T10:00:00+00:00"
        m.save(parent, state)
        selection = {"latest_state": str(self.root / "newer/state.json"), "completed_at": "2026-10-27T11:00:00+00:00"}
        m.save(self.args.session_file, selection)
        m.remember_result(parent, state)
        self.assertEqual(json.loads(self.args.session_file.read_text()), selection)

    def test_unavailable_parent_does_not_submit_paid_request(self):
        parent = self.completed_parent()
        self.args.from_state = parent
        self.args.out_dir = self.root / "retry-edit"
        with patch.object(m, "api_call", return_value={"status": "completed", "detail": {"data": []}}) as api:
            with self.assertRaises(m.UserError):
                m.submit(self.args)
        self.assertEqual([c.args[0] for c in api.call_args_list], ["GET"])


class TemporaryUploadTests(WorkflowCase):
    def run_upload(self, body=None):
        image = self.root / "ref.png"
        Image.new("RGB", (8, 8), "blue").save(image)
        uploaded, checked, client = MagicMock(), MagicMock(), MagicMock()
        uploaded.json.return_value = {"status": "success", "data": {"url": "https://tmpfiles.org/dl/42/ref.png"}}
        checked.is_redirect = False
        checked.iter_content.return_value = [image.read_bytes() if body is None else body]
        checked.__enter__.return_value = checked
        client.post.return_value = uploaded
        client.get.return_value = checked
        with patch.object(m, "session") as factory:
            factory.return_value.__enter__.return_value = client
            result = m.upload_tmpfiles(image, "image/png", ".png")
        return result, client

    def test_temporary_reference_verifies_bytes_and_sets_expiry_without_auth(self):
        (url, metadata), client = self.run_upload()
        self.assertEqual(url, "https://tmpfiles.org/dl/42/ref.png")
        self.assertEqual(metadata["provider"], "tmpfiles")
        from datetime import datetime, timezone
        remaining = (datetime.fromisoformat(metadata["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(3500 < remaining <= 3600)
        self.assertEqual(client.post.call_args.kwargs["data"], {"expire": "3600"})
        self.assertNotIn("headers", client.post.call_args.kwargs)
        self.assertNotIn("headers", client.get.call_args.kwargs)
        self.assertNotIn("url", metadata)

    def test_html_preview_never_becomes_reference(self):
        with self.assertRaises(m.UserError):
            self.run_upload(b"<html>Preview, not original image</html>")

    def test_local_reference_defaults_to_tmpfiles_without_storage(self):
        image = self.root / "ref.png"
        Image.new("RGB", (8, 8), "blue").save(image)
        self.args.image = [image]
        with patch.object(m, "storage", side_effect=AssertionError("Default must not require S3")), patch.object(m, "upload_tmpfiles", return_value=("https://tmpfiles.org/dl/42/ref.png", {"provider": "tmpfiles", "expires_at": "2026-10-27T12:00:00+00:00"}), create=True):
            call = self.create()
        self.assertEqual(call.call_args.args[4]["images"], [{"image_url": "https://tmpfiles.org/dl/42/ref.png"}])
        state = json.loads(self.state_args().state.read_text())
        self.assertEqual(state["uploads"][0]["provider"], "tmpfiles")

    def test_cli_local_image_defaults_to_tmpfiles_and_allows_s3_override(self):
        for extra, expected in (([], "tmpfiles"), (["--upload-provider", "s3"], "s3")):
            with self.subTest(provider=expected), patch.object(m.sys, "argv", ["apinebula_image.py", "submit", "--prompt-file", str(self.prompt), "--out-dir", str(self.root / "cli"), "--image", "ref.png"] + extra), patch.object(m, "submit") as submit:
                self.assertEqual(m.main(), 0)
                self.assertEqual(submit.call_args.args[0].upload_provider, expected)

    def test_tmpfiles_response_must_point_to_provider(self):
        for bad in ("https://evil.example/42/ref.png", "https://user:secret@tmpfiles.org/42/ref.png", "https://tmpfiles.org/", "https://tmpfiles.org/42/ref.png?secret=x"):
            with self.subTest(url=bad), self.assertRaises(m.UserError):
                m.tmpfiles_direct_url(bad)
        self.assertEqual(m.tmpfiles_direct_url("http://tmpfiles.org/42/ref.png"), "https://tmpfiles.org/dl/42/ref.png")
        self.assertEqual(m.tmpfiles_direct_url("https://tmpfiles.org/dl/1793100000.sigN_12/token12/ref.png"), "https://tmpfiles.org/dl/1793100000.sigN_12/token12/ref.png")
        self.assertEqual(m.tmpfiles_direct_url("https://tmpfiles.org/dl/42/ref.png"), "https://tmpfiles.org/dl/42/ref.png")

    def test_upload_failure_never_submits_paid_task(self):
        image = self.root / "ref.png"
        Image.new("RGB", (8, 8), "blue").save(image)
        self.args.image = [image]
        self.args.upload_provider = "tmpfiles"
        with patch.object(m, "upload_tmpfiles", side_effect=m.UserError("Upload failed"), create=True), patch.object(m, "api_call") as api:
            with self.assertRaises(m.UserError):
                m.submit(self.args)
        api.assert_not_called()


class RecoveryAndPollingTests(WorkflowCase):
    def response(self, body, content_type="image/png"):
        response = MagicMock()
        response.is_redirect = False
        response.headers = {"Content-Type": content_type}
        response.iter_content.return_value = [body]
        response.__enter__.return_value = response
        return response

    def test_new_upload_page_resolves_only_matching_download_then_verifies_bytes(self):
        image = self.root / "ref.png"
        Image.new("RGB", (8, 8), "blue").save(image)
        page_url = "https://tmpfiles.org/wSAhLDz8mOim/ref.png"
        direct_url = "https://tmpfiles.org/dl/1793167578.signature/wSAhLDz8mOim/ref.png"
        page = b'<a href="https://evil.example/dl/file">ad</a><a href="https://tmpfiles.org/dl/1793167578.signature/wSAhLDz8mOim/ref.png">Download</a>'
        uploaded = MagicMock()
        uploaded.json.return_value = {"status": "success", "data": {"url": page_url}}
        client = MagicMock()
        client.post.return_value = uploaded
        client.get.side_effect = [self.response(page, "text/html"), self.response(image.read_bytes())]
        with patch.object(m, "session") as factory:
            factory.return_value.__enter__.return_value = client
            url, metadata = m.upload_tmpfiles(image, "image/png", ".png")
        self.assertEqual(url, direct_url)
        self.assertEqual([c.args[0] for c in client.get.call_args_list], [page_url, direct_url])
        self.assertEqual(client.post.call_count, 1)
        self.assertEqual(metadata["provider"], "tmpfiles")

    def test_alphanumeric_page_url_is_not_guessed_as_direct_url(self):
        self.assertEqual(m.tmpfiles_direct_url("https://tmpfiles.org/wSAhLDz8mOim/ref.png"),
                         "https://tmpfiles.org/wSAhLDz8mOim/ref.png")

    def test_resume_reuses_uploaded_page_and_never_reuploads_after_verification_failure(self):
        image = self.root / "ref.png"
        Image.new("RGB", (8, 8), "blue").save(image)
        self.args.image = [image]
        page_url = "https://tmpfiles.org/wSAhLDz8mOim/ref.png"
        direct_url = "https://tmpfiles.org/dl/1793167578.signature/wSAhLDz8mOim/ref.png"
        uploaded = MagicMock()
        uploaded.json.return_value = {"status": "success", "data": {"url": page_url}}
        client = MagicMock()
        client.post.return_value = uploaded
        client.get.side_effect = ConnectionError("interrupted after upload")
        with patch.object(m, "session") as factory, patch.object(m, "api_call") as api:
            factory.return_value.__enter__.return_value = client
            with self.assertRaises(ConnectionError):
                m.submit(self.args)
            api.assert_not_called()
        client.get.side_effect = [self.response(b'<a href="' + direct_url.encode() + b'">Download</a>', "text/html"),
                                  self.response(image.read_bytes())]
        with patch.object(m, "session") as factory, patch.object(m, "api_call", return_value={"id": "task_resumed", "status": "queued"}) as api:
            factory.return_value.__enter__.return_value = client
            self.assertEqual(m.main_with_args(["resume", "--state", str(self.state_args().state)]), 0)
        self.assertEqual(client.post.call_count, 1)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args[4]["images"], [{"image_url": direct_url}])
        state = json.loads(self.state_args().state.read_text())
        self.assertEqual(state["task_id"], "task_resumed")
        self.assertNotIn(page_url, self.state_args().state.read_text())
        self.assertNotIn(direct_url, self.state_args().state.read_text())
        self.assertFalse((self.args.out_dir / "resume.json").exists())

    def test_resume_refuses_uncertain_or_submitted_task_before_any_network(self):
        self.create()
        for phase, task in (("submission_outcome_unknown", None), ("queued", "task_test")):
            state = json.loads(self.state_args().state.read_text())
            state["phase"] = phase
            state.pop("task_id", None)
            if task:
                state["task_id"] = task
            m.save(self.state_args().state, state)
            with patch.object(m, "api_call") as api, patch.object(m, "session") as session:
                self.assertEqual(m.main_with_args(["resume", "--state", str(self.state_args().state)]), 1)
            api.assert_not_called()
            session.assert_not_called()

    def test_bounded_wait_downloads_on_first_completion_without_post(self):
        self.create()
        ticks = iter([0, 0, 0, 10])
        results = [{"status": "queued"}, {"status": "completed", "detail": {"data": [{"download_url": "https://cdn.example.com/out.png"}]}}]
        output = self.args.out_dir / "image.png"
        def downloaded(*args):
            Image.new("RGB", (12, 12)).save(output)
            return output, (12, 12)
        with patch.object(m, "api_call", side_effect=results) as api, patch.object(m, "download", side_effect=downloaded), patch.object(m, "sleep") as sleep, patch.object(m, "monotonic", side_effect=lambda: next(ticks, 10)):
            self.assertEqual(m.main_with_args(["status", "--state", str(self.state_args().state), "--wait", "40", "--interval", "10"]), 0)
        self.assertTrue(output.exists())
        self.assertEqual([c.args[0] for c in api.call_args_list], ["GET", "GET"])
        sleep.assert_called_once_with(10)

    def test_cached_completion_does_not_need_provider_or_key(self):
        self.create()
        output = self.args.out_dir / "image.png"
        Image.new("RGB", (12, 12)).save(output)
        state = json.loads(self.state_args().state.read_text())
        state.update(phase="completed", output="image.png", size=[12, 12])
        m.save(self.state_args().state, state)
        with patch.object(m, "api_call", side_effect=AssertionError("No network for saved result")), patch.object(m, "get_key", side_effect=AssertionError("No key needed")):
            m.status(self.state_args())

    def test_resume_lock_prevents_concurrent_paid_submission(self):
        self.args.out_dir.mkdir()
        m.save(self.state_args().state, {"phase": "reference_uploading", "model": "gpt-image-2.5", "uploads": []})
        (self.args.out_dir / "prompt.txt").write_text("test prompt")
        (self.args.out_dir / "resume.lock").write_text("active")
        m.private_file(self.args.out_dir / "resume.json", json.dumps({"images": [], "sha256": [], "cached": {}, "quality": "high"}))
        with patch.object(m, "api_call") as api:
            self.assertEqual(m.main_with_args(["resume", "--state", str(self.state_args().state)]), 1)
        api.assert_not_called()

    def test_wait_stops_at_deadline_retains_task_and_never_resubmits(self):
        self.create()
        now = [0]
        def advance(seconds):
            now[0] += seconds
        with patch.object(m, "api_call", return_value={"status": "queued"}) as api, patch.object(m, "monotonic", side_effect=lambda: now[0]), patch.object(m, "sleep", side_effect=advance):
            self.assertEqual(m.main_with_args(["status", "--state", str(self.state_args().state), "--wait", "25", "--interval", "10"]), 0)
        self.assertEqual(now[0], 25)
        self.assertTrue(all(c.args[0] == "GET" for c in api.call_args_list))
        self.assertEqual(json.loads(self.state_args().state.read_text())["task_id"], "task_test")

    def test_wait_stops_immediately_on_failed_task(self):
        self.create()
        with patch.object(m, "api_call", return_value={"status": "failed"}) as api, patch.object(m, "sleep") as sleep:
            self.assertEqual(m.main_with_args(["status", "--state", str(self.state_args().state), "--wait", "40"]), 0)
        self.assertEqual(api.call_count, 1)
        sleep.assert_not_called()

    def test_preview_links_cannot_send_reference_to_other_hosts_or_files(self):
        image = self.root / "ref.png"
        Image.new("RGB", (8, 8)).save(image)
        for link in ("https://evil.example/dl/token/ref.png", "https://tmpfiles.org/dl/123/token/other.png", "https://tmpfiles.org/dl/123/other/ref.png", "https://tmpfiles.org.evil.example/dl/token/ref.png"):
            with self.subTest(link=link):
                client = MagicMock()
                client.get.return_value = self.response(('<a href="'+link+'">Download</a>').encode(), "text/html")
                with self.assertRaises(m.UserError):
                    m.verify_tmpfiles(client, "https://tmpfiles.org/token/ref.png", image)
                self.assertEqual(client.get.call_count, 1)

    def test_active_submission_also_blocks_resume_and_another_submit(self):
        self.args.out_dir.mkdir()
        (self.args.out_dir / "resume.lock").write_text("active submit")
        with patch.object(m, "api_call") as api:
            with self.assertRaises(m.UserError):
                m.submit(self.args)
        api.assert_not_called()

if __name__ == "__main__":
    unittest.main()
