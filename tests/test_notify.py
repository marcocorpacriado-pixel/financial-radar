import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import notify

TOKEN = "123:SECRET-TOKEN"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "42"}


class SplitMessageTest(unittest.TestCase):
    def test_short_text_is_one_part(self):
        self.assertEqual(notify.split_message("hola\nradar", limit=20), ["hola\nradar"])

    def test_text_exactly_at_limit_is_one_part(self):
        self.assertEqual(notify.split_message("x" * 10, limit=10), ["x" * 10])

    def test_cuts_on_newlines(self):
        text = "aaaa\nbbbb\ncccc"
        parts = notify.split_message(text, limit=9)
        self.assertEqual(parts, ["aaaa\nbbbb", "cccc"])
        self.assertEqual("\n".join(parts), text)

    def test_long_line_is_hard_cut(self):
        parts = notify.split_message("x" * 25, limit=10)
        self.assertEqual(parts, ["x" * 10, "x" * 10, "x" * 5])

    def test_parts_within_limit_and_content_kept(self):
        text = "\n".join(f"línea {i} " + "y" * (i % 50) for i in range(500))
        parts = notify.split_message(text)
        self.assertTrue(all(0 < len(p) <= 4096 for p in parts))
        self.assertEqual("\n".join(parts), text)

    def test_empty_text_has_no_parts(self):
        self.assertEqual(notify.split_message(""), [])


def ok_response(*args, **kwargs):
    return io.BytesIO(b'{"ok": true}')


@patch.dict(os.environ, ENV)
@patch("notify.urllib.request.urlopen", side_effect=ok_response)
class SendTest(unittest.TestCase):
    def test_payload(self, urlopen):
        notify.send("hola", silent=True)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, f"https://api.telegram.org/bot{TOKEN}/sendMessage")
        self.assertEqual(json.loads(req.data), {"chat_id": "42", "text": "hola", "disable_notification": True})
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)

    def test_not_silent_by_default(self, urlopen):
        notify.send("hola")
        self.assertIs(json.loads(urlopen.call_args.args[0].data)["disable_notification"], False)

    def test_one_request_per_part_in_order(self, urlopen):
        notify.send("a" * 4096 + "\n" + "b" * 10)
        texts = [json.loads(c.args[0].data)["text"] for c in urlopen.call_args_list]
        self.assertEqual(texts, ["a" * 4096, "b" * 10])

    def test_ok_false_raises_without_token(self, urlopen):
        urlopen.side_effect = lambda *a, **k: io.BytesIO(b'{"ok": false, "description": "Bad Request: chat not found"}')
        with self.assertRaises(RuntimeError) as ctx:
            notify.send("hola")
        self.assertIn("chat not found", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_http_error_raises_without_token(self, urlopen):
        body = io.BytesIO(b'{"ok": false, "description": "Unauthorized"}')
        urlopen.side_effect = urllib.error.HTTPError(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 401, "Unauthorized", {}, body)
        with self.assertRaises(RuntimeError) as ctx:
            notify.send("hola")
        self.assertIn("Unauthorized", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    def test_http_error_without_json_body(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError("u", 502, "Bad Gateway", {}, io.BytesIO(b"<html>"))
        with self.assertRaisesRegex(RuntimeError, "502"):
            notify.send("hola")

    def test_missing_env_var_names_it(self, urlopen):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_CHAT_ID"):
                notify.send("hola")
        urlopen.assert_not_called()


class LoadEnvTest(unittest.TestCase):
    def test_loads_without_overriding(self):
        content = '﻿# comentario\n\nA_KEY=uno\nB_KEY="dos=2"\nC_KEY=\'tres\'\nKEEP=nuevo\n'
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            with patch.dict(os.environ, {"KEEP": "viejo"}, clear=True):
                notify.load_env(path)
                self.assertEqual(dict(os.environ), {"A_KEY": "uno", "B_KEY": "dos=2", "C_KEY": "tres", "KEEP": "viejo"})

    def test_missing_file_is_ignored(self):
        notify.load_env(os.path.join(tempfile.gettempdir(), "no-existe-radar.env"))


if __name__ == "__main__":
    unittest.main()
