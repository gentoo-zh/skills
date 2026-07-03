import httpx

from gzh.notify import send_telegram


class _Resp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)


def test_missing_token_skips(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    res = send_telegram("hi")
    assert res["ok"] is False
    assert "TELEGRAM_BOT_TOKEN" in res["error"]


def test_missing_chat_skips(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    res = send_telegram("hi")
    assert res["ok"] is False
    assert "TELEGRAM_CHAT_ID" in res["error"]


def test_send_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp(200)

    monkeypatch.setattr("gzh.notify.httpx.post", fake_post)
    res = send_telegram("hello")
    assert res["ok"] is True
    assert res["status"] == 200
    assert "/bot" in captured["url"] and "sendMessage" in captured["url"]
    assert captured["json"]["text"] == "hello"
    assert captured["json"]["parse_mode"] == "Markdown"


def test_send_api_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr("gzh.notify.httpx.post", lambda *a, **k: _Resp(400))
    res = send_telegram("hi")
    assert res["ok"] is False
    assert res["status"] == 400


from click.testing import CliRunner

from gzh.cli import cli


def test_notify_telegram_help_registered():
    result = CliRunner().invoke(cli, ["notify", "telegram", "--help"])
    assert result.exit_code == 0


def test_notify_telegram_missing_token_exits_zero(monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = CliRunner().invoke(cli_mod.cli,
                                ["notify", "telegram", "--message", "hi"])
    assert result.exit_code == 0  # non-fatal
    assert '"ok": false' in result.output
