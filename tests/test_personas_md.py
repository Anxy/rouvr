"""Persona MD files: loaded beside the registry, aliased coder/reviewer."""
from pathlib import Path

from rouvr.personas import PersonaRegistry

YAML = """
tenants:
  t:
    personas:
      coder-1: {role: coder, principal: svc-b, muse_model: '', credential_file: /tmp/b.json}
      reviewer-1: {role: reviewer, principal: svc-v, muse_model: '', credential_file: /tmp/v.json}
"""


def test_instructions_alias_and_name(tmp_path: Path):
    (tmp_path / "coder.md").write_text("CODER PERSONA")
    (tmp_path / "reviewer.md").write_text("REVIEWER PERSONA")
    reg_path = tmp_path / "tenants.yaml"
    reg_path.write_text(YAML)
    reg = PersonaRegistry.load(reg_path)
    assert reg.get("t", "coder-1", "coder").instructions == "CODER PERSONA"
    assert reg.get("t", "reviewer-1", "reviewer").instructions == "REVIEWER PERSONA"


def test_name_file_wins_and_missing_is_empty(tmp_path: Path):
    (tmp_path / "coder-1.md").write_text("NAMED")
    (tmp_path / "coder.md").write_text("ALIAS")
    reg_path = tmp_path / "tenants.yaml"
    reg_path.write_text(YAML)
    reg = PersonaRegistry.load(reg_path)
    assert reg.get("t", "coder-1", "coder").instructions == "NAMED"
    assert reg.get("t", "reviewer-1", "reviewer").instructions == ""


def test_repo_persona_files_exist():
    root = Path(__file__).parent.parent / "personas"
    assert "coder" in (root / "coder.md").read_text().lower()
    assert "reviewer" in (root / "reviewer.md").read_text().lower()
