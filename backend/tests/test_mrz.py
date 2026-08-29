"""S3 — MRZ check-digit validator tests.

Each test FAILS against pre-S3 code: bot/mrz.py does not exist pre-S3.

test_valid_td3_passes: a known-good TD3 pair → all fields.
test_each_field_corruption_fails: flip one digit in doc/DOB/expiry/composite
  → validate() returns None (four cases).
"""
from __future__ import annotations

import pytest

from app.bot.mrz import MrzFields, check_digit, parse_td3, validate


# A known-good TD3 MRZ pair (ICAO example, adjusted for valid check digits).
# This is a synthetic passport for testing — not a real document.
# Line 1: P<SGPTAN<<WEILING<<<<<<<<<<<<<<<<<<<<<<<<
# Line 2: E1234567<8SGP9001011M3001019<<<<<<<<<<<<<<4
#
# We need to compute valid check digits. Let me build them properly.

def _build_valid_mrz(dob: str = "900101") -> tuple[str, str]:
    """Build a valid TD3 MRZ pair with correct check digits.

    `dob` is the YYMMDD date-of-birth field; check digits are computed
    from it so any DOB (past or future) stays checksum-valid.
    """
    # Line 1
    issuing = "SGP"
    name_field = "TAN<<WEILING"
    line1 = "P<" + issuing + name_field + "<" * (44 - 5 - len(name_field))

    # Line 2 components
    doc_number = "E12345674"  # 9 chars (we'll compute check digit)
    doc_raw = doc_number[:9]
    cd_doc = check_digit(doc_raw)

    nationality = "SGP"
    cd_dob = check_digit(dob)

    sex = "M"
    expiry = "300101"  # 2030-01-01
    cd_expiry = check_digit(expiry)

    personal = "<" * 14
    cd_personal = check_digit(personal)

    composite_data = (
        doc_raw + cd_doc + dob + cd_dob + expiry + cd_expiry
        + personal + cd_personal
    )
    cd_composite = check_digit(composite_data)

    line2 = (
        doc_raw + cd_doc + nationality + dob + cd_dob
        + sex + expiry + cd_expiry + personal + cd_personal + cd_composite
    )
    assert len(line1) == 44, f"line1 length {len(line1)}"
    assert len(line2) == 44, f"line2 length {len(line2)}"
    return line1, line2


VALID_LINE1, VALID_LINE2 = _build_valid_mrz()


class TestValidTD3:
    """A known-good TD3 pair parses and validates successfully."""

    def test_valid_td3_passes(self):
        result = parse_td3(VALID_LINE1, VALID_LINE2)
        assert result is not None
        assert isinstance(result, MrzFields)
        assert result.family_name == "TAN"
        assert result.given_name == "WEILING"
        assert result.gender == "M"
        assert result.birthday == "1990-01-01"
        assert result.nationality_iso2 == "SG"  # SGP → SG
        assert result.doc_number  # non-empty
        assert result.issuing_country == "SG"
        assert result.doc_expiry == "2030-01-01"

    def test_validate_with_raw_dict(self):
        """validate() with mrz_line1/mrz_line2 keys works."""
        result = validate({
            "mrz_line1": VALID_LINE1,
            "mrz_line2": VALID_LINE2,
        })
        assert result is not None
        assert result.family_name == "TAN"


class TestFieldCorruption:
    """Flipping one digit in each check-digited field → validate() returns None."""

    def _corrupt(self, line: str, pos: int) -> str:
        """Flip one digit at `pos` (0→1, else 0)."""
        ch = line[pos]
        replacement = "1" if ch == "0" else "0"
        return line[:pos] + replacement + line[pos + 1:]

    def test_doc_number_corruption_fails(self):
        """Flip a digit in the doc_number → check digit mismatch → None."""
        corrupted = self._corrupt(VALID_LINE2, 0)  # first char of doc_number
        result = parse_td3(VALID_LINE1, corrupted)
        assert result is None

    def test_dob_corruption_fails(self):
        """Flip a digit in the date of birth → check digit mismatch → None."""
        corrupted = self._corrupt(VALID_LINE2, 13)  # first char of DOB
        result = parse_td3(VALID_LINE1, corrupted)
        assert result is None

    def test_expiry_corruption_fails(self):
        """Flip a digit in the expiry date → check digit mismatch → None."""
        corrupted = self._corrupt(VALID_LINE2, 21)  # first char of expiry
        result = parse_td3(VALID_LINE1, corrupted)
        assert result is None

    def test_composite_corruption_fails(self):
        """Flip the composite check digit → None."""
        corrupted = self._corrupt(VALID_LINE2, 43)  # composite CD
        result = parse_td3(VALID_LINE1, corrupted)
        assert result is None


class TestEdgeCases:
    """Structural validation edge cases."""

    def test_wrong_length_rejected(self):
        assert parse_td3("short", VALID_LINE2) is None
        assert parse_td3(VALID_LINE1, "short") is None

    def test_empty_dict_validate_none(self):
        assert validate({}) is None
        assert validate({"mrz_line1": "", "mrz_line2": ""}) is None

    def test_illegal_char_does_not_raise(self):
        """OCR noise (an illegal char in a check-digited field) → None,
        never a raised exception (the caller's fallback must run)."""
        # '@' is not in the MRZ alphabet.
        bad = "@" + VALID_LINE2[1:]
        assert parse_td3(VALID_LINE1, bad) is None
        assert check_digit  # sanity: check_digit imported

    def test_non_string_ocr_output_validate_none(self):
        """A non-string VL output degrades to None, never raises."""
        assert validate({"mrz_line1": 12345, "mrz_line2": None}) is None

    def test_expired_passport_rejected(self):
        """A checksum-valid but EXPIRED passport fails closed at the gate."""
        # Build a valid MRZ with an expiry in the past (2010-01-01).
        issuing = "SGP"
        name_field = "TAN<<WEILING"
        line1 = "P<" + issuing + name_field + "<" * (44 - 5 - len(name_field))
        doc_raw = "E12345674"
        cd_doc = check_digit(doc_raw)
        dob = "900101"
        cd_dob = check_digit(dob)
        expiry = "100101"  # 2010-01-01 — expired
        cd_expiry = check_digit(expiry)
        personal = "<" * 14
        cd_personal = check_digit(personal)
        composite = doc_raw + cd_doc + dob + cd_dob + expiry + cd_expiry + personal + cd_personal
        cd_composite = check_digit(composite)
        line2 = (
            doc_raw + cd_doc + "SGP" + dob + cd_dob + "M"
            + expiry + cd_expiry + personal + cd_personal + cd_composite
        )
        assert parse_td3(line1, line2) is None

    def test_future_birthday_rejected(self):
        """A checksum-valid TD3 whose DOB is in the FUTURE fails closed at
        the gate (mirror of the expiry-not-past rule)."""
        from datetime import date, timedelta
        # ~400 days ahead, encoded as YYMMDD so the test never rots.
        future_dob = date.today() + timedelta(days=400)
        dob = f"{future_dob.year % 100:02d}{future_dob.month:02d}{future_dob.day:02d}"
        line1, line2 = _build_valid_mrz(dob=dob)
        assert parse_td3(line1, line2) is None


class TestNationalityFailClosed:
    """ISO-3 → ISO-2 mapping is fail-closed; alias codes with filler map."""

    def test_unmapped_iso3_fails_closed(self):
        """An ISO-3 code not in the curated CSV → parse_td3 returns None
        (typed-entry fallback), never free text into a write."""
        issuing = "SGP"
        name_field = "TAN<<WEILING"
        line1 = "P<" + issuing + name_field + "<" * (44 - 5 - len(name_field))
        doc_raw = "E12345674"
        cd_doc = check_digit(doc_raw)
        dob = "900101"
        cd_dob = check_digit(dob)
        expiry = "300101"
        cd_expiry = check_digit(expiry)
        personal = "<" * 14
        cd_personal = check_digit(personal)
        # ZZZ is not a real ISO-3 code.
        composite = doc_raw + cd_doc + dob + cd_dob + expiry + cd_expiry + personal + cd_personal
        cd_composite = check_digit(composite)
        line2 = (
            doc_raw + cd_doc + "ZZZ" + dob + cd_dob + "M"
            + expiry + cd_expiry + personal + cd_personal + cd_composite
        )
        assert parse_td3(line1, line2) is None

    def test_alias_code_with_filler_maps(self):
        """A padded alias nationality (German 'D<<') resolves, not shunted
        to typed entry."""
        from app.bot.mrz import _iso3_to_iso2
        assert _iso3_to_iso2("D<<") == "DE"
        assert _iso3_to_iso2("DEU") == "DE"


class TestTypedEntryGate:
    """The typed path routes through the SAME gate module."""

    def test_validate_typed_nationality_accept_and_reject(self):
        from app.bot.mrz import validate_typed_nationality
        assert validate_typed_nationality("SG") == "SG"
        assert validate_typed_nationality("sg") == "SG"
        assert validate_typed_nationality("ZZ") is None   # not in CSV
        assert validate_typed_nationality("SGP") is None  # wrong length

    def test_build_typed_fields_accept(self):
        from app.bot.mrz import build_typed_fields
        fields = build_typed_fields(
            "tan", "weiling", "m", "1990-01-01", "SG",
            "e1234567", "SG", "2030-01-01",
        )
        assert fields is not None
        assert fields.family_name == "TAN"
        assert fields.nationality_iso2 == "SG"
        assert fields.doc_number == "E1234567"

    def test_build_typed_fields_rejects_bad_nationality(self):
        from app.bot.mrz import build_typed_fields
        assert build_typed_fields(
            "tan", "wei", "m", "1990-01-01", "ZZ",
            "e1", "SG", "2030-01-01",
        ) is None

    def test_build_typed_fields_rejects_expired(self):
        from app.bot.mrz import build_typed_fields
        assert build_typed_fields(
            "tan", "wei", "m", "1990-01-01", "SG",
            "e1", "SG", "2010-01-01",  # expired
        ) is None

    def test_build_typed_fields_rejects_future_birthday(self):
        from datetime import date, timedelta
        from app.bot.mrz import build_typed_fields
        # ~1 year ahead, computed from today so the test never rots.
        future_dob = (date.today() + timedelta(days=365)).isoformat()
        assert build_typed_fields(
            "tan", "wei", "m", future_dob, "SG",
            "e1", "SG", "2030-01-01",
        ) is None

    def test_build_typed_fields_rejects_bad_calendar_date(self):
        from app.bot.mrz import build_typed_fields
        assert build_typed_fields(
            "tan", "wei", "m", "1990-02-30", "SG",  # Feb 30
            "e1", "SG", "2030-01-01",
        ) is None


# ---------------------------------------------------------------------------
# Real-incident golden MRZ (Qwen-VL extraction of a Singapore passport).
# Provably valid: check digits pass on the canonical 44/44 pair.  The bug
# was that vision-model output deviates on FILLER (dropped trailing '<',
# spaces instead of '<'), and the old validate() hard-gated on len == 44.

GOLDEN_NAME_BASE = "PASGPKUA<<HONG<YIK<JAYDON"  # 25 chars, no name filler
GOLDEN_LINE1 = GOLDEN_NAME_BASE + "<" * (44 - len(GOLDEN_NAME_BASE))
GOLDEN_LINE2 = "K3907018B1SGP0503291M3303023T0507554D<<<<<44"

# EXACT raw pair captured live from qwen-vl-max on the incident photo:
# line 1 carries one EXTRA trailing '<' (45 chars — existing trim form
# rescues it); line 2 is 43 chars because ONE '<' was dropped from the
# INTERIOR filler run of the personal-number field (needs the
# check-digit-gated '<'-insertion repair).
INCIDENT_LINE1_RAW = GOLDEN_NAME_BASE + "<" * 20  # 45 chars
INCIDENT_LINE2_RAW = "K3907018B1SGP0503291M3303023T0507554D<<<<44"  # 43


class TestGoldenIncidentMrz:
    """The real incident MRZ must validate, and normalization must never
    weaken the gate."""

    def test_canonical_pair_passes(self):
        assert len(GOLDEN_LINE1) == 44
        assert len(GOLDEN_LINE2) == 44
        result = validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": GOLDEN_LINE2,
        })
        assert result is not None
        assert result.family_name == "KUA"
        assert result.given_name == "HONG YIK JAYDON"
        assert result.gender == "M"
        assert result.birthday == "2005-03-29"
        assert result.nationality_iso2 == "SG"
        assert result.doc_number == "K3907018B"
        assert result.issuing_country == "SG"
        assert result.doc_expiry == "2033-03-02"

    def test_line1_missing_trailing_filler_passes(self):
        """VL output dropped the line-1 name padding (27-ish chars, not 44)."""
        result = validate({
            "mrz_line1": GOLDEN_NAME_BASE,  # 25 chars, filler omitted
            "mrz_line2": GOLDEN_LINE2,
        })
        assert result is not None
        assert result.family_name == "KUA"

    def test_leading_trailing_spaces_pass(self):
        result = validate({
            "mrz_line1": "  " + GOLDEN_LINE1 + "  ",
            "mrz_line2": " " + GOLDEN_LINE2 + "\n",
        })
        assert result is not None
        assert result.given_name == "HONG YIK JAYDON"

    def test_spaces_used_as_filler_pass(self):
        """Spaces instead of '<' in the line-1 name padding."""
        line1_spaces = GOLDEN_NAME_BASE + " " * (44 - len(GOLDEN_NAME_BASE))
        result = validate({
            "mrz_line1": line1_spaces,
            "mrz_line2": GOLDEN_LINE2,
        })
        assert result is not None
        assert result.family_name == "KUA"

    def test_extra_trailing_filler_line2_pass(self):
        """A 45-char line2 whose extra char is a trailing '<'."""
        assert len(GOLDEN_LINE2 + "<") == 45
        result = validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": GOLDEN_LINE2 + "<",
        })
        assert result is not None
        assert result.doc_number == "K3907018B"

    def test_line2_interior_spaces_as_filler_pass(self):
        """Line 2 with spaces INSIDE the personal-number filler (positions
        29-42) instead of '<'.  Position-preserving mapping must rescue it;
        deletion-form normalization would shift the digit columns and break
        the check digits."""
        line2_spaces = "K3907018B1SGP0503291M3303023T0507554D     44"
        assert len(line2_spaces) == 44
        result = validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": line2_spaces,
        })
        assert result is not None
        assert result.family_name == "KUA"
        assert result.given_name == "HONG YIK JAYDON"
        assert result.birthday == "2005-03-29"
        assert result.doc_number == "K3907018B"
        assert result.doc_expiry == "2033-03-02"

    def test_line1_spaces_between_name_tokens_pass(self):
        """Line 1 with spaces between given-name tokens (unpadded).  The
        mapped form reconstructs 'HONG<YIK<JAYDON' — given_name must come
        out with spaces, never merged into one token (the old
        whitespace-deletion form corrupted this PII)."""
        line1_spaces = "P<SGPKUA<<HONG YIK JAYDON"  # 26 chars, no filler
        result = validate({
            "mrz_line1": line1_spaces,
            "mrz_line2": GOLDEN_LINE2,
        })
        assert result is not None
        assert result.given_name == "HONG YIK JAYDON"
        assert result.given_name != "HONGYIKJAYDON"
        assert result.family_name == "KUA"

    def test_combined_line1_spaces_and_line2_trailing_filler_pass(self):
        """Both deviations at once: line 1 unpadded with spaces between
        name tokens AND line 2 carrying an extra trailing '<'."""
        result = validate({
            "mrz_line1": "P<SGPKUA<<HONG YIK JAYDON",
            "mrz_line2": GOLDEN_LINE2 + "<",  # 45 chars, filler excess
        })
        assert result is not None
        assert result.given_name == "HONG YIK JAYDON"
        assert result.doc_number == "K3907018B"

    def test_line1_extra_trailing_filler_pass(self):
        """Line 1 = golden line 1 + one extra trailing '<' (45 chars).
        Trim-variant symmetry with the line-2 trailing-filler case."""
        overlong = GOLDEN_LINE1 + "<"
        assert len(overlong) == 45
        result = validate({
            "mrz_line1": overlong,
            "mrz_line2": GOLDEN_LINE2,
        })
        assert result is not None
        assert result.family_name == "KUA"
        assert result.given_name == "HONG YIK JAYDON"

    def test_o_for_zero_still_rejected(self):
        """'O' substituted for '0' in the DOB → check-digit mismatch → None.
        Normalization must not rescue content errors."""
        corrupted = GOLDEN_LINE2[:13] + "O" + GOLDEN_LINE2[14:]
        assert validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": corrupted,
        }) is None

    def test_wrong_stated_check_digit_still_rejected(self):
        """Flip the stated DOB check digit (pos 19) → mismatch → None."""
        stated = GOLDEN_LINE2[19]
        flipped = "0" if stated != "0" else "1"
        corrupted = GOLDEN_LINE2[:19] + flipped + GOLDEN_LINE2[20:]
        assert validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": corrupted,
        }) is None

    def test_interior_corruption_still_rejected(self):
        """Flip one digit inside the doc number → doc check digit (and
        composite) no longer match → None."""
        corrupted = ("4" if GOLDEN_LINE2[0] != "4" else "5") + GOLDEN_LINE2[1:]
        assert validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": corrupted,
        }) is None

    def test_overlong_non_filler_excess_fails_closed(self):
        """A 45-char line2 whose extra char is NOT trailing '<' filler is
        never truncated — it fails closed at the length gate."""
        corrupted = GOLDEN_LINE2[:43] + "A" + GOLDEN_LINE2[43]  # 45 chars
        assert len(corrupted) == 45
        assert validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": corrupted,
        }) is None

    # ---- Filler-insertion repair (dropped interior '<' on line 2) ----

    def test_exact_incident_pair_passes(self):
        """The EXACT raw VL output (45/43) validates with all golden
        fields: line 1 via trailing-filler trim, line 2 via the
        check-digit-gated '<'-insertion repair."""
        assert len(INCIDENT_LINE1_RAW) == 45
        assert len(INCIDENT_LINE2_RAW) == 43
        result = validate({
            "mrz_line1": INCIDENT_LINE1_RAW,
            "mrz_line2": INCIDENT_LINE2_RAW,
        })
        assert result is not None
        assert result.family_name == "KUA"
        assert result.given_name == "HONG YIK JAYDON"
        assert result.gender == "M"
        assert result.birthday == "2005-03-29"
        assert result.nationality_iso2 == "SG"
        assert result.doc_number == "K3907018B"
        assert result.issuing_country == "SG"
        assert result.doc_expiry == "2033-03-02"

    def test_two_dropped_fillers_line2_passes(self):
        """A 42-char line 2 with TWO '<' missing from the interior filler
        run (k=2 combinations) is repaired and passes."""
        line2_42 = GOLDEN_LINE2[:37] + "<<<" + GOLDEN_LINE2[42:]
        assert len(line2_42) == 42
        result = validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": line2_42,
        })
        assert result is not None
        assert result.doc_number == "K3907018B"
        assert result.given_name == "HONG YIK JAYDON"
        assert result.doc_expiry == "2033-03-02"

    def test_spaces_plus_two_dropped_fillers_line2_passes(self):
        """Combined deviation: a 42-char line 2 using SPACES as filler AND
        missing TWO '<' from the interior filler run.  Validates with the
        golden fields via whitespace→'<' mapping plus the check-digit-gated
        '<'-insertion repair.  Regression test for the mapped-seed-first
        enumeration order in _filler_repair_forms (space-bearing raw forms
        can never pass the check-digit gate and must not consume the
        candidate cap ahead of the mapped seed's valid proposals)."""
        line2_42 = "K3907018B1SGP0503291M3303023T0507554D  <44"
        assert len(line2_42) == 42
        result = validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": line2_42,
        })
        assert result is not None
        assert result.family_name == "KUA"
        assert result.given_name == "HONG YIK JAYDON"
        assert result.gender == "M"
        assert result.birthday == "2005-03-29"
        assert result.nationality_iso2 == "SG"
        assert result.doc_number == "K3907018B"
        assert result.issuing_country == "SG"
        assert result.doc_expiry == "2033-03-02"

    def test_line1_never_gets_filler_insertion_repair(self):
        """Line-1 invariant: filler-INSERTION repair never applies to
        line 1.  A 42-char line 1 missing ONE '<' from the surname/given
        separator ('KUA<HONG' instead of 'KUA<<HONG') plus one trailing
        filler: the ONLY candidate forms line 1 receives are
        whitespace→'<' mapping and '<' PADDING, and padding cannot
        re-insert the dropped separator '<', so the '<<' split fails and
        the whole name field collapses into family_name.  Had '<'
        insertion repair been allowed on line 1, a candidate would
        restore the separator and yield family_name 'KUA' — this
        assertion would fail.  Line 1 has no check digits, so nothing
        could gate such fabricated content."""
        short_line1 = "PASGPKUA<HONG<YIK<JAYDON" + "<" * 18
        assert len(short_line1) == 42
        result = validate({
            "mrz_line1": short_line1,
            "mrz_line2": GOLDEN_LINE2,
        })
        assert result is not None          # padding still yields a parse
        assert result.family_name == "KUA HONG YIK JAYDON"  # NOT repaired
        assert result.family_name != "KUA"

    def test_dropped_real_content_char_fails_closed(self):
        """A 43-char line2 made by deleting a REAL content character (the
        '4' from the personal number 'T0507554D') must NOT be repaired —
        insertion only proposes '<', and the check digits reject every
        candidate."""
        corrupted = GOLDEN_LINE2[:35] + GOLDEN_LINE2[36:]  # drop the '4'
        assert len(corrupted) == 43
        assert corrupted == "K3907018B1SGP0503291M3303023T050755D<<<<<44"
        assert validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": corrupted,
        }) is None

    def test_dropped_dob_digit_fails_closed(self):
        """A 43-char line2 missing a DOB digit: the repair window starts
        at column 28, so the missing char (outside the window) can never
        be re-inserted → None."""
        corrupted = GOLDEN_LINE2[:14] + GOLDEN_LINE2[15:]  # drop DOB '5'
        assert len(corrupted) == 43
        assert validate({
            "mrz_line1": GOLDEN_LINE1,
            "mrz_line2": corrupted,
        }) is None
