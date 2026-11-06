"""Unit tests for VS Code's settings reader, lib/claude_vm/editors/vscode.py's vscode_setting, which its plan calls."""

from __future__ import annotations

import pytest

from claude_vm.editors import vscode as vscode_filters

jsonc_loads = vscode_filters.jsonc_loads
vscode_setting = vscode_filters.vscode_setting

KEY = "task.allowAutomaticTasks"


class TestJsoncLoads:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param('{"a": 1, "b": [1, 2]}', {"a": 1, "b": [1, 2]}, id="plain-json"),
            pytest.param(
                """{
          // a line comment
          "a": 1, /* a block
          comment */ "b": 2 // trailing
        }""",
                {"a": 1, "b": 2},
                id="line-and-block-comments",
            ),
            pytest.param(
                '{"a": [1, 2,], "b": {"c": 3,},}',
                {"a": [1, 2], "b": {"c": 3}},
                id="trailing-commas-in-objects-and-arrays",
            ),
            pytest.param('{"a": 1, // last\n}', {"a": 1}, id="trailing-comma-before-a-line-comment"),
            pytest.param('{"a": 1, /* last */ }', {"a": 1}, id="trailing-comma-before-a-block-comment"),
            pytest.param(
                '{"url": "https://example.com/a//b", "glob": "/*.js", "odd": ",}"}',
                {"url": "https://example.com/a//b", "glob": "/*.js", "odd": ",}"},
                id="comment-markers-inside-strings-are-text",
            ),
            pytest.param(
                '{\n  // set "x" to "y", or leave it\n  "a": 1 /* "b": 2 */\n}',
                {"a": 1},
                id="quotes-inside-comments-do-not-start-strings",
            ),
            # One escaped quote before the "//", so a scanner that took it for the end of the string would read a
            # comment from there.
            pytest.param(
                r'{"a": "say \"hi // not a comment\""}',
                {"a": 'say "hi // not a comment"'},
                id="escaped-quotes-stay-inside-their-string",
            ),
            pytest.param(
                r'{"a": "x\"", "url": "http://x//y"}',
                {"a": 'x"', "url": "http://x//y"},
                id="an-escaped-quote-does-not-end-the-string",
            ),
            pytest.param(
                r'{"a": "x\\", "url": "http://x//y"}',
                {"a": "x\\", "url": "http://x//y"},
                id="an-escaped-backslash-before-the-closing-quote",
            ),
            pytest.param(
                r'{"a": "x\"", "b": "y\\", "c": ["p", "q"], "url": "http://x//y",}',
                {"a": 'x"', "b": "y\\", "c": ["p", "q"], "url": "http://x//y"},
                id="escapes-and-string-arrays-in-one-file",
            ),
            pytest.param('\ufeff{"a": 1}', {"a": 1}, id="a-byte-order-mark-is-ignored"),
        ],
    )
    def test_reads(self, text, expected):
        assert jsonc_loads(text) == expected

    def test_a_comma_before_a_string_is_kept(self):
        assert jsonc_loads('["a", "b"]') == ["a", "b"]
        text = '{"cSpell.words": ["ansible", "proxmox"], "task.allowAutomaticTasks": "off"}'
        assert vscode_setting(text, KEY) == "off"

    def test_broken_text_raises(self):
        with pytest.raises(ValueError):
            jsonc_loads('{"a": 1')
        with pytest.raises(ValueError):
            jsonc_loads('{"a": "unterminated}')


class TestVscodeSetting:
    @pytest.mark.parametrize("value", ["on", "off"])
    def test_reads_the_flat_key(self, value):
        assert vscode_setting(f'{{"{KEY}": "{value}"}}', KEY) == value

    def test_absent_key_gives_the_default(self):
        assert vscode_setting('{"editor.fontSize": 14}', KEY) == ""
        assert vscode_setting('{"editor.fontSize": 14}', KEY, "unset") == "unset"

    def test_a_commented_out_key_does_not_count(self):
        text = f'{{\n  // "{KEY}": "on",\n  "editor.fontSize": 14,\n}}'
        assert vscode_setting(text, KEY, "") == ""

    def test_a_block_commented_key_does_not_count(self):
        assert vscode_setting(f'{{ /* "{KEY}": "off" */ }}', KEY, "") == ""

    def test_odd_spacing_and_a_trailing_comma(self):
        text = f'{{\n\t"{KEY}"\n  :\t"on"  ,\n}}'
        assert vscode_setting(text, KEY) == "on"

    def test_the_last_of_duplicate_keys_wins(self):
        assert vscode_setting(f'{{"{KEY}": "on", "{KEY}": "off"}}', KEY) == "off"

    def test_the_nested_spelling(self):
        assert vscode_setting('{"task": {"allowAutomaticTasks": "on"}}', KEY) == "on"
        assert vscode_setting('{"task": {"quickOpen.skip": true}}', KEY, "") == ""
        assert vscode_setting('{"task": "on"}', KEY, "") == ""

    def test_the_later_spelling_wins_as_vs_code_reads_it(self):
        # Keys apply in file order: a nested object replaces what the flat
        # key built, and a flat key reaches into a nested object.
        assert vscode_setting(f'{{"task": {{"allowAutomaticTasks": "on"}}, "{KEY}": "off"}}', KEY) == "off"
        assert vscode_setting(f'{{"{KEY}": "on", "task": {{"allowAutomaticTasks": "off"}}}}', KEY) == "off"
        assert vscode_setting(f'{{"task": {{"quickOpen.skip": true}}, "{KEY}": "on"}}', KEY) == "on"

    def test_a_key_through_a_value_that_is_not_an_object_is_dropped(self):
        # VS Code logs a conflict and drops the key.
        assert vscode_setting(f'{{"task": null, "{KEY}": "off"}}', KEY, "") == ""
        assert vscode_setting(f'{{"task": "x", "{KEY}": "off"}}', KEY, "") == ""

    @pytest.mark.parametrize("text", ["", None, "   "])
    def test_empty_text_gives_the_default(self, text):
        assert vscode_setting(text, KEY) == ""
        assert vscode_setting(text, KEY, "unset") == "unset"

    @pytest.mark.parametrize("text", ["not json", '{"a": ', "[1, 2]", '"on"', '{"a": 1\n "b": 2}'])
    def test_text_that_does_not_parse_or_is_not_an_object_is_told_apart(self, text):
        assert vscode_setting(text, KEY) == "unparsable"
        assert vscode_setting(text, KEY, "", unparsable="broken") == "broken"

    def test_a_real_looking_settings_file(self):
        text = """// Place your settings in this file to overwrite the default settings
{
    "workbench.colorTheme": "Default Dark Modern",
    "files.exclude": {
        "**/.git": true, // keep "these" out
    },
    /* Ask once, then remember: */
    "task.allowAutomaticTasks": "on",
    "remote.SSH.defaultExtensions": ["anthropic.claude-code",],
}
"""
        assert vscode_setting(text, KEY) == "on"
        assert vscode_setting(text, "remote.SSH.defaultExtensions") == ["anthropic.claude-code"]
