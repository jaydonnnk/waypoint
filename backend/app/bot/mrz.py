"""ICAO 9303 TD3 (passport) MRZ check-digit validator + field parser.

Pure, deterministic, no I/O.  The acceptance gate — validate() is the ONLY
path by which a photo becomes a stored traveler.

TD3 layout (two lines of 44 characters each):
  Line 1: P<ISSUING_COUNTRY<SURNAME<<GIVEN_NAMES<<<...
  Line 2: DOC_NUMBER<CD_DOC NATIONALITY DOB CD_DOB SEX EXPIRY CD_EXPIRY PERSONAL_NUMBER<CD_PERSONAL CD_COMPOSITE

Check digits use 7-3-1 cyclic weights, mod 10.

ISO-3→ISO-2 nationality: FAIL-CLOSED — unknown ISO-3 → None → typed-entry
fallback.  The typed fallback ALSO validates nationality against the same
curated CSV (never free text).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.data.loaders import load_iso3_to_iso2

# 7-3-1 cyclic weights per ICAO 9303.
_WEIGHTS = (7, 3, 1)

# Character value table (ICAO 9303 §4.9).
_CHAR_VALUES: dict[str, int] = {"<": 0}
for _i in range(10):
    _CHAR_VALUES[str(_i)] = _i
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ", start=10):
    _CHAR_VALUES[_c] = _i


@dataclass(frozen=True)
class MrzFields:
    """Validated passport MRZ fields (all strings, ready for DB storage)."""

    family_name: str
    given_name: str
    gender: str          # "M" / "F" / "X"
    birthday: str        # "YYYY-MM-DD"
    nationality_iso2: str
    doc_number: str
    issuing_country: str  # ISO-2
    doc_expiry: str       # "YYYY-MM-DD"


def check_digit(data: str) -> str:
    """Compute the ICAO 7-3-1 weighted check digit for `data`.

    Returns a single character '0'–'9'.  Every character in data must be
    in A–Z, 0–9, or '<'.
    """
    total = 0
    for i, ch in enumerate(data):
        val = _CHAR_VALUES.get(ch)
        if val is None:
            raise ValueError(f"illegal MRZ character: {ch!r}")
        total += val * _WEIGHTS[i % 3]
    return str(total % 10)


def _mrz_date_to_iso(raw: str, century_pivot: int = 30) -> str | None:
    """Convert a 6-digit MRZ date (YYMMDD) to YYYY-MM-DD.

    Century pivot: YY < pivot → 20YY, else 19YY.  Returns None on
    structurally invalid input (wrong length, non-digit) OR a date that
    does not exist on the calendar (e.g. Feb 30) — real calendar
    validation via datetime, not a loose 1..31 range check.
    """
    if len(raw) != 6 or not raw.isdigit():
        return None
    yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
    yyyy = 2000 + yy if yy < century_pivot else 1900 + yy
    try:
        date(yyyy, mm, dd)  # rejects Feb 30, month 13, day 0, etc.
    except ValueError:
        return None
    return f"{yyyy:04d}-{mm:02d}-{dd:02d}"


def _iso3_to_iso2(code: str) -> str | None:
    """Map MRZ ISO-3 nationality to ISO-2. FAIL-CLOSED: unmapped → None.

    Strips MRZ filler '<' before lookup so alias codes padded to 3 chars
    (e.g. German 'D<<') resolve — otherwise a whole issuing country is
    silently shunted to the typed-entry fallback.
    """
    cleaned = code.replace("<", "").strip().upper()
    if not cleaned:
        return None
    return load_iso3_to_iso2().get(cleaned)


def _clean_name_component(raw: str) -> str:
    """Strip MRZ fillers and trim."""
    return raw.replace("<", " ").strip()


def parse_td3(line1: str, line2: str) -> MrzFields | None:
    """Parse a TD3 MRZ (two 44-char lines) into validated fields.

    Returns None on ANY structural or check-digit failure — the caller
    falls back to typed entry.  NEVER raises: illegal characters in a
    check-digited field (OCR noise) are caught and treated as invalid,
    so the caller's fallback path always runs (a raise here would strand
    the traveler with silence).
    """
    try:
        return _parse_td3_inner(line1, line2)
    except (ValueError, TypeError, IndexError):
        return None


def _parse_td3_inner(line1: str, line2: str) -> MrzFields | None:
    if len(line1) != 44 or len(line2) != 44:
        return None

    # ---- Line 1: document type + issuing state + name -----------------
    # doc_type_raw = line1[0:2]  # "P<" for passports
    issuing_iso3 = line1[2:5]
    name_field = line1[5:44]
    name_parts = name_field.split("<<", 1)
    if len(name_parts) < 2:
        return None
    family_name = _clean_name_component(name_parts[0])
    given_name = _clean_name_component(name_parts[1])
    if not family_name:
        return None

    # ---- Line 2: machine-readable fields + check digits ---------------
    doc_number = line2[0:9]
    cd_doc = line2[9]
    nationality_iso3 = line2[10:13]
    dob_raw = line2[13:19]
    cd_dob = line2[19]
    sex = line2[20]
    expiry_raw = line2[21:27]
    cd_expiry = line2[27]
    personal_number = line2[28:42]
    cd_personal = line2[42]
    cd_composite = line2[43]

    # ---- Check digits (four of them) ----------------------------------
    # 1. Document number
    if check_digit(doc_number) != cd_doc:
        return None
    # 2. Date of birth
    if check_digit(dob_raw) != cd_dob:
        return None
    # 3. Expiry date
    if check_digit(expiry_raw) != cd_expiry:
        return None
    # 4. Composite (doc_number + cd_doc + dob + cd_dob + expiry + cd_expiry + personal_number + cd_personal)
    composite_data = (
        doc_number + cd_doc + dob_raw + cd_dob + expiry_raw + cd_expiry
        + personal_number + cd_personal
    )
    if check_digit(composite_data) != cd_composite:
        return None

    # ---- Convert fields -----------------------------------------------
    birthday = _mrz_date_to_iso(dob_raw)
    doc_expiry = _mrz_date_to_iso(expiry_raw, century_pivot=60)
    if birthday is None or doc_expiry is None:
        return None

    # Fail closed on a future birthday — mirror of the expiry-not-past
    # check below.  A DOB that hasn't happened yet can never belong to a
    # real traveler.
    if datetime.strptime(birthday, "%Y-%m-%d").date() > date.today():
        return None

    # Fail closed on an already-expired passport — never let a
    # checksum-valid but expired document into a booking (it would fail
    # late at Atlas instead of at the gate).
    if datetime.strptime(doc_expiry, "%Y-%m-%d").date() < date.today():
        return None

    nationality_iso2 = _iso3_to_iso2(nationality_iso3)
    if nationality_iso2 is None:
        return None  # FAIL-CLOSED: unmapped → typed-entry fallback

    issuing_iso2 = _iso3_to_iso2(issuing_iso3)
    if issuing_iso2 is None:
        return None  # FAIL-CLOSED on issuing country too

    gender = {"M": "M", "F": "F", "<": "X"}.get(sex)
    if gender is None:
        return None

    # Strip filler '<' from doc_number for clean storage.
    clean_doc = doc_number.replace("<", "").strip()
    if not clean_doc:
        return None

    return MrzFields(
        family_name=family_name,
        given_name=given_name or family_name,  # single-name passports
        gender=gender,
        birthday=birthday,
        nationality_iso2=nationality_iso2,
        doc_number=clean_doc,
        issuing_country=issuing_iso2,
        doc_expiry=doc_expiry,
    )


def validate(fields_raw: dict) -> MrzFields | None:
    """Validate a raw OCR dict via ICAO check digits.

    Expects keys 'mrz_line1' and 'mrz_line2' (full 44-char MRZ lines).
    Returns MrzFields on success, None on any failure.  NEVER raises —
    a non-dict or non-string VL output degrades to None so the caller's
    typed-entry fallback always runs.
    validate() is the ONLY path by which a photo becomes a stored traveler.
    """
    try:
        line1 = str(fields_raw.get("mrz_line1") or "").strip().upper()
        line2 = str(fields_raw.get("mrz_line2") or "").strip().upper()
    except (AttributeError, TypeError):
        return None
    if not line1 or not line2:
        return None
    return parse_td3(line1, line2)


def validate_typed_nationality(code: str) -> str | None:
    """Validate a hand-typed ISO-2 nationality against the curated CSV.

    FAIL-CLOSED: returns the ISO-2 code if it appears as a VALUE in the
    curated mapping, None otherwise.  Never free text.
    """
    code = code.strip().upper()
    if len(code) != 2:
        return None
    valid = set(load_iso3_to_iso2().values())
    return code if code in valid else None


def _valid_iso_date(value: str) -> str | None:
    """Parse a hand-typed YYYY-MM-DD via real calendar validation."""
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    return parsed.isoformat()


def build_typed_fields(
    family_name: str,
    given_name: str,
    gender: str,
    birthday: str,
    nationality: str,
    doc_number: str,
    issuing_country: str,
    doc_expiry: str,
) -> MrzFields | None:
    """Build MrzFields from hand-typed entry, applying the SAME gate rules
    as parse_td3: curated-CSV nationality (fail-closed, never free text),
    real calendar dates, DOB-not-future, expiry-not-past, M/F gender.  Returns None on any
    failure so the typed path is exactly as strict as the photo path.
    """
    gender = gender.strip().upper()
    if gender not in ("M", "F"):
        return None

    nat = validate_typed_nationality(nationality)
    if nat is None:
        return None
    iss = validate_typed_nationality(issuing_country)
    if iss is None:
        return None

    dob = _valid_iso_date(birthday)
    exp = _valid_iso_date(doc_expiry)
    if dob is None or exp is None:
        return None
    # Fail closed on a future birthday (same as the photo gate).
    if datetime.strptime(dob, "%Y-%m-%d").date() > date.today():
        return None
    # Fail closed on an already-expired document (same as the photo gate).
    if datetime.strptime(exp, "%Y-%m-%d").date() < date.today():
        return None

    doc = doc_number.strip().upper()
    family = family_name.strip().upper()
    given = given_name.strip().upper()
    if not doc or not family:
        return None

    return MrzFields(
        family_name=family,
        given_name=given or family,
        gender=gender,
        birthday=dob,
        nationality_iso2=nat,
        doc_number=doc,
        issuing_country=iss,
        doc_expiry=exp,
    )
