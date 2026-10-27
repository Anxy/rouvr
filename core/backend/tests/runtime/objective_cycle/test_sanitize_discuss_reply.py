from backend.infrastructure.model_gateway.intelligence_layer.service import sanitize_discuss_reply


def test_sanitize_keeps_short_replies():
    text = "I can propose a bounded_repair for a registration page. Click Create & run with AIX."
    assert sanitize_discuss_reply(text) == text


def test_sanitize_strips_html_document_dump():
    dump = """<!DOCTYPE html>
<html lang="en">
<head><title>Registration Form</title></head>
<body><form><input type="password"></form></body>
</html>"""
    out = sanitize_discuss_reply(dump)
    assert "<!DOCTYPE" not in out
    assert "Create & run" in out


def test_sanitize_strips_fenced_html():
    dump = "Sure!\n```html\n<html><body>hi</body></html>\n```\n"
    out = sanitize_discuss_reply(dump)
    assert "```html" not in out.lower()
    assert "preview link" in out.lower() or "Create & run" in out
