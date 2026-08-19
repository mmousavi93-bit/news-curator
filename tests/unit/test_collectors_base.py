from __future__ import annotations

from agent.collectors.base import decode_body, hash_raw, resolve_charset, strip_html


def test_strip_html_removes_tags_scripts_styles_comments_and_unescapes():
    raw = (
        "<style>.x{color:red}</style>"
        "<!-- a comment -->"
        "<p>Hello &amp; <b>world</b></p>"
        "<script>alert('x')</script>"
        "  extra   spaces  "
    )
    assert strip_html(raw) == "Hello & world extra spaces"


def test_strip_html_unwraps_cdata_before_stripping_tags():
    # The common WordPress/wire-syndication shape: <description><![CDATA[<p>...</p>]]></description>.
    # Without CDATA-aware handling, the literal run "<![CDATA[<p>" is treated as one tag
    # (the CDATA's own '<' plus the first real tag's '>' close it early) and the
    # trailing "]]>" leaks into the output with no error raised.
    raw = "<![CDATA[<p>Body two, from a content:encoded block.</p>]]>"
    assert strip_html(raw) == "Body two, from a content:encoded block."


def test_strip_html_empty_input_is_empty_string():
    assert strip_html("") == ""
    assert strip_html("   ") == ""


def test_strip_html_collapses_space_left_by_a_tag_before_punctuation():
    # <b>word</b>. -- the tag is replaced by a space, and the real text has
    # no space of its own before the period, so the naive result is
    # "word ." with a dangling space. Common shape: markup around the last
    # word of a sentence. Cosmetic, but it reads as sloppy against the tone
    # contract ("a calm, knowledgeable friend"), so it is collapsed rather
    # than left for the composer to notice per-cluster.
    raw = "<p>Body one with &amp; an entity and <b>markup</b>.</p>"
    assert strip_html(raw) == "Body one with & an entity and markup."


def test_resolve_charset_content_type_wins_over_xml_prolog():
    body = b'<?xml version="1.0" encoding="iso-8859-1"?><rss></rss>'
    assert resolve_charset("text/xml; charset=windows-1256", body) == "windows-1256"


def test_resolve_charset_falls_back_to_xml_prolog_encoding():
    body = b'<?xml version="1.0" encoding="ISO-8859-1"?><rss></rss>'
    assert resolve_charset("text/xml", body) == "ISO-8859-1"


def test_resolve_charset_defaults_to_utf8_with_no_hints():
    assert resolve_charset("text/html", b"<html></html>") == "utf-8"


def test_resolve_charset_windows1256_arabic_is_live_for_this_project():
    # windows-1256 is a real, currently-configured charset for Arabic sources
    # (session notes). A wrong decode here is silent mojibake with no error.
    body = "مرحبا".encode("windows-1256")
    assert resolve_charset("text/html; charset=windows-1256", body) == "windows-1256"
    assert decode_body("text/html; charset=windows-1256", body) == "مرحبا"


def test_decode_body_unknown_charset_falls_back_to_utf8_instead_of_raising():
    body = "hello".encode("utf-8")
    # "bogus-charset" is not a real codec name -> LookupError inside decode_body,
    # caught and retried as utf-8, never propagated to the caller.
    assert decode_body("text/html; charset=bogus-charset", body) == "hello"


def test_hash_raw_is_deterministic():
    a = hash_raw(b"same bytes")
    b = hash_raw(b"same bytes")
    assert a == b
    assert a != hash_raw(b"different bytes")


def test_hash_raw_is_over_raw_bytes_not_normalised_text():
    # Two entries that normalise to the identical stripped body must still
    # hash differently if their raw bytes differ -- hashing must happen
    # BEFORE normalisation, or a future change to strip_html would silently
    # invalidate every stored raw_hash (Phase 4 dedup depends on this).
    raw_a = b"<title>X</title><description>Y</description>"
    raw_b = b"<title>X</title>\n<description>Y</description>"  # one extra newline
    assert strip_html(raw_a.decode()) == strip_html(raw_b.decode())
    assert hash_raw(raw_a) != hash_raw(raw_b)
