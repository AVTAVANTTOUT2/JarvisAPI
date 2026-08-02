"""Tests extraction texte iMessage (attributedBody)."""

from integrations.imessage_body import decode_attributed_body, message_text_from_row


def test_message_text_prefers_plain_text():
    assert message_text_from_row("Bonjour", b"ignored") == "Bonjour"


def test_message_text_decodes_attributed_body():
    blob = (
        b"streamtyped\x00\x03\x01NSString\x00"
        b"Bonjour depuis attributedBody\x00"
    )
    assert message_text_from_row(None, blob) == "Bonjour depuis attributedBody"


def test_decode_attributed_body_ignores_class_names():
    blob = b"streamtyped\x00NSMutableAttributedString\x00Hello world\x00NSString"
    assert decode_attributed_body(blob) == "Hello world"
