"""
Formats and sends alerts to the single tagged Telegram feed.

Output format (per spec) -- every alert carries whichever of these tags are
relevant to it, in this order:

[Chain: X] [Score: N/100 or HIGH-RISK MOMENTUM] [Deployer: elite/good]
[Convergence: N wallets] [SELL: name/role sold X%]
[Backing: verified real/needs-verification]
[News: source] [Exit-risk: reason]
[Realizable: $Y actual vs $Z theoretical, N% slippage]
[MEGA-ALERT: N layers within Xmin]

This module does NOT place any trade, ever -- it only sends a message.
"""
import requests
from config import CONFIG
from links import render_links_line


class Alert:
    def __init__(self, token_symbol: str, token_address: str, chain: str, headline: str):
        self.token_symbol = token_symbol
        self.token_address = token_address
        self.chain = chain
        self.headline = headline
        self.tags: dict = {}

    def set_tag(self, key: str, value: str):
        if value:
            self.tags[key] = value
        return self

    def render(self) -> str:
        order = [
            "Chain", "Score", "Deployer", "Convergence", "SELL",
            "Backing", "Buzz", "News", "Exit-risk", "Realizable", "MEGA-ALERT",
        ]
        tag_str = " ".join(f"[{k}: {self.tags[k]}]" for k in order if k in self.tags)
        lines = [
            f"🚨 {self.headline}",
            f"{self.token_symbol}  `{self.token_address}`",
            tag_str,
        ]
        links_line = render_links_line(self.chain, self.token_address)
        if links_line:
            lines.append(links_line)
        return "\n".join(lines)


def send_telegram_message(text: str) -> dict:
    if not CONFIG.telegram_ready():
        return {"sent": False, "reason": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured"}
    url = f"https://api.telegram.org/bot{CONFIG.telegram_bot_token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": CONFIG.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=CONFIG.http_timeout_seconds,
    )
    ok = resp.status_code == 200
    return {"sent": ok, "status_code": resp.status_code, "body": resp.text[:500]}


def send_alert(alert: Alert) -> dict:
    return send_telegram_message(alert.render())
