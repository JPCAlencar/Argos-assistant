import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import ProviderConfig, RuntimeConfig, Settings
from app.security import SecretStore, fingerprint, redact_data
from app.tools.filesystem import FileScope
from app.tools.registry import NoArguments, Tool, ToolError, ToolRegistry
from app.workflows import WorkflowDefinition, WorkflowStep, substitute

pytestmark = pytest.mark.unit


def test_config_rejects_sqlite():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite:///argos.db")


def test_limits_and_official_endpoint():
    with pytest.raises(ValidationError):
        RuntimeConfig(max_steps=0)
    with pytest.raises(ValidationError):
        ProviderConfig(base_url="https://attacker.example")
    with pytest.raises(ValidationError):
        ProviderConfig(input_price=0)


def test_secret_encryption_and_exact_approval():
    store = SecretStore(Fernet.generate_key().decode())
    encrypted = store.encrypt("private-key")
    assert "private-key" not in encrypted
    assert store.decrypt(encrypted) == "private-key"
    assert fingerprint("write_file", {"path": "a", "content": "x"}) != fingerprint(
        "write_file", {"path": "a", "content": "y"}
    )
    assert redact_data({"nested": ["password=bad-value", "sk-abcdefghijkl"]}) == {
        "nested": ["[segredo omitido]", "[segredo omitido]"]
    }


def test_file_scope_blocks_escape_secrets_network_and_hardlinks(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    scope = FileScope([root])
    assert scope.resolve("note.txt") == root / "note.txt"
    for value in (
        "../outside.txt",
        ".env",
        ".ssh/key",
        "my-password.txt",
        "file.txt:secret",
        "\\\\server\\file",
    ):
        with pytest.raises(ToolError):
            scope.resolve(value)
    original = root / "original.txt"
    original.write_text("data")
    link = root / "link.txt"
    link.hardlink_to(original)
    with pytest.raises(ToolError):
        scope.resolve(str(link))


def test_symlink_escape_when_supported(tmp_path):
    root, outside = tmp_path / "inside", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Sistema não permite criação de symlinks pelo usuário.")
    with pytest.raises(ToolError):
        FileScope([root]).resolve("link/secret.txt")


def test_registry_no_unimplemented_tools_and_duplicate():
    registry = ToolRegistry()

    async def handler(args):
        return {"confirmed": True}

    registry.register(Tool("status", "Status", NoArguments, handler))
    with pytest.raises(ValueError):
        registry.register(Tool("status", "Duplicate", NoArguments, handler))
    with pytest.raises(ToolError):
        registry.get("execute_codex_task")
    with pytest.raises(ValidationError):
        NoArguments.model_validate({"shell": "whoami"})


def test_workflow_parameters_external_refs_and_substitution():
    with pytest.raises(ValueError):
        WorkflowDefinition(
            parameters={"type": "object", "$ref": "https://example.com/schema"},
            steps=[WorkflowStep(tool="get_system_status")],
        )
    assert substitute({"path": "{{project}}", "n": "{{count}}"}, {"project": "a", "count": 2}) == {
        "path": "a",
        "n": 2,
    }
