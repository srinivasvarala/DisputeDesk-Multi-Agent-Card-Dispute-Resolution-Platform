import pytest

from disputedesk.config import Settings


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent of the developer's shell: unset every env var Settings reads."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def make_settings(**overrides: object) -> Settings:
    """Settings from defaults + overrides only (no .env file, env already cleared)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def anyio_backend() -> str:
    """Run `@pytest.mark.anyio` tests on asyncio only (the anyio plugin ships with anyio)."""
    return "asyncio"
