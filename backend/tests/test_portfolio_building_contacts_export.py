from scripts.export_portfolio_building_contacts import (
    _flatten_contact_rows,
    normalize_company_key,
)


def test_normalize_company_key_matches_venture_variants():
    assert normalize_company_key("VENTURE NY PROPERTY MANAGEMENT, LLC") == (
        "VENTURENYPROPERTYMANAGEMENTLLC"
    )
    assert normalize_company_key("Venture NY Property Management") == (
        "VENTURENYPROPERTYMANAGEMENT"
    )


def test_flatten_contact_rows_preserves_building_and_source_context():
    rows = _flatten_contact_rows([
        {
            "bbl": "3023587501",
            "address": "100 NORTH 3 STREET",
            "borough": "BROOKLYN",
            "zip_code": "11249",
            "unit_count": 24,
            "churn_score": 3.5,
            "churn_category": "stable",
            "management_company": "VENTURE NY PROPERTY MANAGEMENT, LLC",
            "corporate_owner": "100 N 3 CORP",
            "dos_contacts_status": "not_loaded",
            "contacts": [
                {
                    "name": "VENTURE NY PROPERTY MANAGEMENT, LLC",
                    "role": "Agent",
                    "source": "HPD Registration",
                    "as_of_date": "2026-02-23",
                    "address": "43-10 11TH STREET, Long Island City, NY, 11101",
                    "source_record_id": "123",
                    "source_url": "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
                    "is_decision_maker": False,
                }
            ],
        }
    ])

    assert rows == [
        {
            "bbl": "3023587501",
            "address": "100 NORTH 3 STREET",
            "borough": "BROOKLYN",
            "zip_code": "11249",
            "unit_count": 24,
            "churn_score": 3.5,
            "churn_category": "stable",
            "management_company": "VENTURE NY PROPERTY MANAGEMENT, LLC",
            "corporate_owner": "100 N 3 CORP",
            "dos_contacts_status": "not_loaded",
            "contact_name": "VENTURE NY PROPERTY MANAGEMENT, LLC",
            "contact_role": "Agent",
            "contact_source": "HPD Registration",
            "contact_updated": "2026-02-23",
            "contact_address": "43-10 11TH STREET, Long Island City, NY, 11101",
            "contact_confidence": "--",
            "contact_source_record_id": "123",
            "contact_source_url": "https://data.cityofnewyork.us/resource/feu5-w2e2.json",
            "board_role": None,
            "is_decision_maker": False,
        }
    ]
