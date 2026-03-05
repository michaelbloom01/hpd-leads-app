from src.tasks.enrich import _build_dos_cache_payload


class _FakeEntity:
    dos_id = "12345"
    name = "TEST OWNER LLC"
    entity_type = "DOMESTIC LLC"
    ceo_name = "John Doe"
    ceo_address = "1 Main St, New York, NY 10001"


def test_build_dos_cache_payload_shape():
    payload = _build_dos_cache_payload(
        corporate_owner_name="TEST OWNER LLC",
        dos_entity=_FakeEntity(),
        officers=[{"name": "Officer A", "filing_num": "F-1"}],
    )

    assert payload["lookup_name"] == "TEST OWNER LLC"
    assert payload["dos_id"] == "12345"
    assert payload["entity_name"] == "TEST OWNER LLC"
    assert payload["ceo_name"] == "John Doe"
    assert isinstance(payload["officers"], list)
    assert payload["officers"][0]["filing_num"] == "F-1"
    assert payload["snapshot_as_of"]
