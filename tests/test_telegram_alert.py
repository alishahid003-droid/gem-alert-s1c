from telegram_alert import Alert


def test_alert_render_tag_order_and_content():
    a = Alert("SGEM", "0xabc123", "base", "High-risk momentum spike")
    a.set_tag("Chain", "Base") \
     .set_tag("Score", "HIGH-RISK MOMENTUM") \
     .set_tag("Convergence", "3 wallets") \
     .set_tag("Backing", "needs-verification")
    text = a.render()
    assert "[Chain: Base]" in text
    assert "[Score: HIGH-RISK MOMENTUM]" in text
    # order must follow the spec's tag order: Chain before Score before Convergence before Backing
    assert text.index("[Chain:") < text.index("[Score:") < text.index("[Convergence:") < text.index("[Backing:")


def test_alert_omits_absent_tags():
    a = Alert("X", "addr", "solana", "headline")
    a.set_tag("Chain", "Solana")
    text = a.render()
    assert "[SELL:" not in text
    assert "[MEGA-ALERT:" not in text
