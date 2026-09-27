"""Offline tests for the sole-trader data minimisation in lookup_by_nip.

Run: python tests/test_privacy.py   (or: python -m pytest tests)
No network: fixtures only.
"""

import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location("server", pathlib.Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

SOLE_TRADER = {
    "name": "JAN PRZYKŁADOWY USŁUGI",
    "nip": "1111111111",
    "statusVat": "Czynny",
    "regon": "123456789",
    "pesel": None,
    "krs": None,
    "residenceAddress": "UL. KWIATOWA 5/2, 00-001 WARSZAWA",
    "workingAddress": None,
    "representatives": [],
    "authorizedClerks": [],
    "partners": [],
    "registrationLegalDate": "2020-01-01",
    "accountNumbers": ["11111111111111111111111111", "22222222222222222222222222"],
    "hasVirtualAccounts": False,
}

COMPANY = {
    "name": "PRZYKŁAD SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
    "nip": "2222222222",
    "statusVat": "Czynny",
    "regon": "987654321",
    "krs": "0000000001",
    "workingAddress": "UL. DŁUGA 1, 00-002 KRAKÓW",
    "residenceAddress": None,
    "representatives": [{"firstName": "ANNA", "lastName": "PRZYKŁADOWA", "pesel": "00000000000"}],
    "authorizedClerks": [],
    "partners": [],
    "registrationLegalDate": "2019-05-05",
    "accountNumbers": ["33333333333333333333333333"],
    "hasVirtualAccounts": True,
}


def test_sole_trader_is_minimised():
    out, privacy = server._minimize_subject(SOLE_TRADER)
    assert out == {
        "name": "JAN PRZYKŁADOWY USŁUGI",
        "nip": "1111111111",
        "statusVat": "Czynny",
        "krs": None,
        "city": "WARSZAWA",
        "accountCount": 2,
        "accountCheck": {
            "tool": "check_bank_account",
            "arguments": {"nip": "1111111111", "account": "<26-digit account number>"},
        },
    }
    assert privacy["naturalPerson"] is True
    assert privacy["hidden"] == ["accountNumbers", "residenceAddress", "regon", "registrationLegalDate"]
    assert privacy["officialSource"] == server.MF_SEARCH_PAGE
    text = str(out)
    for secret in ("KWIATOWA", "123456789", "1111111111111111", "2020-01-01"):
        assert secret not in text, secret


def test_sole_trader_without_accounts_has_no_account_check():
    subject = dict(SOLE_TRADER, accountNumbers=[])
    out, privacy = server._minimize_subject(subject)
    assert out["accountCount"] == 0
    assert "accountCheck" not in out
    assert "accountNumbers" not in privacy["hidden"]


def test_company_whitelist_drops_people():
    out, privacy = server._minimize_subject(COMPANY)
    assert privacy is None
    assert out["regon"] == "987654321"
    assert out["workingAddress"] == "UL. DŁUGA 1, 00-002 KRAKÓW"
    assert out["accountNumbers"] == ["33333333333333333333333333"]
    assert "representatives" not in out
    assert "00000000000" not in str(out)
    assert "lookup_by_krs" in out["note"]


def test_nip_result_shapes():
    found = server._nip_result({"result": {"subject": SOLE_TRADER, "requestId": "r1", "requestDateTime": "t"}}, "2026-09-27")
    assert found["found"] is True
    assert found["privacy"]["naturalPerson"] is True
    missing = server._nip_result({"result": {"subject": None, "requestId": "r2", "requestDateTime": "t"}}, "2026-09-27")
    assert missing == {
        "date": "2026-09-27",
        "found": False,
        "subject": None,
        "privacy": None,
        "requestId": "r2",
        "requestDateTime": "t",
    }
    error = {"error": {"code": "WL-115", "message": "Nieprawidłowa data."}}
    assert server._nip_result(error, "2026-09-27") == error


def test_city_from_address():
    city = server._city_from_address
    assert city("UL. KWIATOWA 5, 00-001 WARSZAWA") == "WARSZAWA"
    assert city("00-001 WARSZAWA UL. KWIATOWA") == "WARSZAWA"
    # Street with a house number after the town: no confident town, so none (same rule as skanfirmy.pl).
    assert city("00-001 WARSZAWA UL. KWIATOWA 5") is None
    assert city("RYNEK 1, 30-001 KRAKÓW-NOWA HUTA") == "KRAKÓW-NOWA HUTA"
    assert city("KWIATOWA 5\n00-001 WARSZAWA") == "WARSZAWA"
    assert city("UL. KWIATOWA 5, WARSZAWA") is None
    assert city(None) is None


def test_validate_account():
    assert server._validate_account("PL61 1090 1014 0000 0712 1981 2874") == "61109010140000071219812874"
    for bad in ("123", "PL61 1090 1014 0000 0712 1981 287X"):
        try:
            server._validate_account(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid account {bad!r}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"OK — {len(tests)} tests passed")
