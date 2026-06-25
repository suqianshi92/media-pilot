from media_pilot import adapters, config, file_tools, orchestration, repository, web


def test_stage_zero_module_boundaries_are_importable() -> None:
    boundaries = {
        orchestration.MODULE_BOUNDARY,
        config.MODULE_BOUNDARY,
        repository.MODULE_BOUNDARY,
        file_tools.MODULE_BOUNDARY,
        adapters.MODULE_BOUNDARY,
        web.MODULE_BOUNDARY,
    }

    assert boundaries == {
        "workflow orchestration",
        "runtime configuration",
        "database repositories",
        "controlled file operations",
        "external service adapters",
        "operator web interface",
    }
