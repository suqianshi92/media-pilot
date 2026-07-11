from media_pilot.resource_discovery.types import ResourceCandidate
from media_pilot.services.candidate_cache import lookup_candidate, store_candidate


def test_candidate_handles_are_scoped_to_creator_and_adult_permission() -> None:
    candidate = ResourceCandidate(
        title="Adult Movie",
        indexer="test",
        source="prowlarr",
        download_url="https://example.test/adult.torrent",
    )
    token = store_candidate(
        candidate,
        owner_user_id="alice",
        is_adult=True,
    )

    assert lookup_candidate(
        token,
        owner_user_id="bob",
        can_access_adult=True,
    )[0] is None
    assert lookup_candidate(
        token,
        owner_user_id="alice",
        can_access_adult=False,
    )[0] is None
    assert lookup_candidate(
        token,
        owner_user_id="alice",
        can_access_adult=True,
    )[0] is candidate
