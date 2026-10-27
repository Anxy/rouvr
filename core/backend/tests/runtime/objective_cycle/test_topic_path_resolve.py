"""Topic phrase → file binding for outcome-only objectives."""

from pathlib import Path

from backend.aix_core.intelligence.repository.topic_path_resolve import (
    extract_topic_snake_hints,
    resolve_topic_hints_in_repository,
    topic_hits_are_mutation_scope,
)


def test_feature_flag_topic_resolves_fixture_files(tmp_path: Path) -> None:
    fixtures = tmp_path / "backend" / "tests" / "live_fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "feature_flag.py").write_text("FEATURE = {}\n", encoding="utf-8")
    (fixtures / "test_feature_flag.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    objective = (
        "Add support for an optional expiry on a feature flag; keep existing "
        "enabled behavior when expires_at is missing."
    )
    hints = extract_topic_snake_hints(objective)
    assert "feature_flag" in hints
    resolved = resolve_topic_hints_in_repository(tmp_path, hints, objective=objective)
    assert any(p.endswith("feature_flag.py") for p in resolved)
    assert any("test_feature_flag.py" in p for p in resolved)
    assert topic_hits_are_mutation_scope(resolved, objective=objective) is True


def test_email_verification_live_fixture_prefers_db_not_platform_verifier(tmp_path: Path) -> None:
    db = tmp_path / "backend" / "tests" / "aix_core" / "objective_cycle" / "live_fixtures" / "db"
    db.mkdir(parents=True)
    (db / "models.py").write_text("class FixtureUser: ...\n", encoding="utf-8")
    (db / "migrate.py").write_text("def bootstrap(): ...\n", encoding="utf-8")
    (db / "schema.sql").write_text("CREATE TABLE fixture_users (id INTEGER);\n", encoding="utf-8")
    (db / "test_db_users.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    platform = tmp_path / "backend" / "aix_core" / "objective_cycle"
    platform.mkdir(parents=True)
    (platform / "verification.py").write_text("class V: ...\n", encoding="utf-8")
    objective = (
        "Add email verification support to the live-fixture user store. "
        "Discover the database schema and migration approach yourself."
    )
    hints = extract_topic_snake_hints(objective)
    assert "verification" not in hints or "email_verified" in hints
    resolved = resolve_topic_hints_in_repository(tmp_path, hints, objective=objective)
    assert any("live_fixtures/db" in p.replace("\\", "/") for p in resolved)
    assert not any(p.endswith("verification.py") for p in resolved[:4])


def test_items_api_live_fixture_prefers_api_not_db(tmp_path: Path) -> None:
    root = tmp_path / "backend" / "tests" / "aix_core" / "objective_cycle" / "live_fixtures"
    api = root / "api"
    db = root / "db"
    api.mkdir(parents=True)
    db.mkdir(parents=True)
    (api / "app.py").write_text("async def app(): ...\n", encoding="utf-8")
    (api / "service.py").write_text("class ItemStore: ...\n", encoding="utf-8")
    (api / "test_api_items.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (db / "models.py").write_text("class FixtureUser: ...\n", encoding="utf-8")
    (db / "test_db_users.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    objective = (
        "Add archive support to the live-fixture items API so a client can archive "
        "an item. Run the focused items API tests until they pass."
    )
    hints = extract_topic_snake_hints(objective)
    assert "test_api_items" in hints or "items" in hints
    resolved = resolve_topic_hints_in_repository(tmp_path, hints, objective=objective)
    assert any("live_fixtures/api" in p.replace("\\", "/") for p in resolved)
    assert not any("live_fixtures/db" in p.replace("\\", "/") for p in resolved)
    assert topic_hits_are_mutation_scope(resolved, objective=objective) is True


def test_production_router_authority_does_not_bind_items_api_fixture(tmp_path: Path) -> None:
    api = (
        tmp_path
        / "backend"
        / "tests"
        / "runtime"
        / "objective_cycle"
        / "live_fixtures"
        / "api"
    )
    api.mkdir(parents=True)
    (api / "schemas.py").write_text("MATRIX = {}\n", encoding="utf-8")
    (api / "test_api_items.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    routers = tmp_path / "backend" / "routers"
    routers.mkdir(parents=True)
    (routers / "objective_cycle_router.py").write_text("router = None\n", encoding="utf-8")
    (tmp_path / "backend" / "main.py").write_text("app = None\n", encoding="utf-8")
    objective = (
        "Explore the live AIX backend and classify every mounted public router "
        "by actual production behavior. Persist an endpoint authority matrix "
        "and prove retired mutation routes stay fail-closed."
    )
    hints = extract_topic_snake_hints(objective)
    assert "test_api_items" not in hints
    resolved = resolve_topic_hints_in_repository(tmp_path, hints, objective=objective)
    assert not any("live_fixtures/api" in p.replace("\\", "/") for p in resolved)


def test_repo_path_in_objective_does_not_bind_generic_service_modules(tmp_path: Path) -> None:
    routers = tmp_path / "backend" / "routers"
    routers.mkdir(parents=True)
    (routers / "objective_cycle_router.py").write_text("router = None\n", encoding="utf-8")
    (tmp_path / "backend" / "main.py").write_text("app = None\n", encoding="utf-8")
    (tmp_path / "backend" / "aix_core" / "runtime" / "objective").mkdir(parents=True)
    (
        tmp_path / "backend" / "aix_core" / "runtime" / "objective" / "endpoint_classification.py"
    ).write_text("class C: ...\n", encoding="utf-8")
    decoy = tmp_path / "backend" / "aix_orchestrator_v2"
    decoy.mkdir(parents=True)
    (decoy / "service.py").write_text("class S: ...\n", encoding="utf-8")
    objective = (
        r"Explore the live AIX backend at D:\v2.1\aix\hyeve-aix-service and classify "
        "every mounted public router. Persist a machine-readable endpoint authority "
        "matrix in the backend and prove retired mutation routes stay fail-closed "
        "while the objective-cycle path remains the live writer."
    )
    hints = extract_topic_snake_hints(objective)
    assert "service" not in hints
    assert "aix_service" not in hints
    resolved = resolve_topic_hints_in_repository(tmp_path, hints, objective=objective)
    joined = [p.replace("\\", "/") for p in resolved]
    assert any(p.endswith("objective_cycle_router.py") or p.endswith("main.py") or "endpoint_classification.py" in p for p in joined)
    assert not any(p.endswith("service.py") for p in joined)
    assert topic_hits_are_mutation_scope(resolved, objective=objective) is False
