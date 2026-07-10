def is_adult_metadata_selection(
    *,
    profile: str | None,
    provider: str | None,
) -> bool:
    return provider == "tpdb" or bool(profile and "adult" in profile.casefold())
