from backend.aix_core.intelligence.repository.repository_location import rebind_repository_for_objective


class _S:
    OBJECTIVE_CYCLE_REPOSITORY_ROOTS = (
        '{"hyeve-integration-service": "D:/v2.1/hyeve/services/hyeve-integration-service", '
        '"aix-frontend": "D:/v2.1/AIX/hyeve-aix-service/AIX-Frontend", '
        '"visions": "D:/v2.1/Visions"}'
    )


def test_absolute_visions_path_rebinds_away_from_aix_frontend() -> None:
    repo, rel = rebind_repository_for_objective(
        "aix-frontend",
        r'update "D:\v2.1\Visions\bivr-generator.html"',
        target_path=r"D:\v2.1\Visions\bivr-generator.html",
        settings=_S(),
    )
    assert repo == "visions"
    assert rel.replace("\\", "/") == "bivr-generator.html"
