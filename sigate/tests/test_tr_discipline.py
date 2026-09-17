"""
Enforces the tr() discipline rule from sigate_dev_workflow.md's coding
rules: every user-facing string literal in the ui layer must be wrapped
in self.tr(...), so translation files can be generated from a complete,
reliable set of source strings. This is checked with a real AST parse
rather than a regex, since a regex over multi-line calls proved
unreliable when this check was first done by hand.
"""

import ast
from pathlib import Path

_UI_DIR = Path(__file__).parent.parent / "ui"

# Constructor/method names whose first string argument is commonly
# user-facing display text.
_TEXT_SETTING_CALLS = {
    "QLabel",
    "setText",
    "setPlaceholderText",
    "setWindowTitle",
    "setToolTip",
    "addItem",
}


def _find_unwrapped_string_literals(source: str) -> list:
    """Returns a list of (line_number, literal_value) for every bare
    string-literal argument passed directly to one of the known
    text-setting calls, without going through self.tr(...) first."""
    tree = ast.parse(source)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name not in _TEXT_SETTING_CALLS:
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.strip()
            ):
                violations.append((node.lineno, arg.value))
    return violations


def test_every_ui_file_wraps_user_facing_strings_in_tr():
    all_violations = {}
    for path in sorted(_UI_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        violations = _find_unwrapped_string_literals(source)
        if violations:
            all_violations[path.name] = violations

    assert not all_violations, (
        "Found user-facing string literal(s) not wrapped in self.tr(...): "
        f"{all_violations}. Wrap each in self.tr(...) before this check will pass."
    )
