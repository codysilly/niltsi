"""Conservative runway association. These rules do not establish availability."""
import re

RUNWAY = r"(?:3[0-6]|[12][0-9]|0?[1-9])[LRC]?"
SUBJECT = re.compile(r"\bRWY\s+(" + RUNWAY + r"(?:\s*(?:/|,|AND)\s*(?:RWY\s+)?" + RUNWAY + r")*)\s+(.+)")


def normalize(value):
    return value.zfill(3 if value[-1:] in "LRC" else 2)


def classify(record, inventory):
    text = " ".join(record["text"].upper().split())
    result = dict(record, runway_ends=[], classification="unresolved", reason="UNSUPPORTED_TEXT")
    if len(re.findall(r"\bRWY\b", text)) > 1:
        result["reason"] = "MULTIPLE_RUNWAY_CLAUSES"
        return result
    # Match a runway subject, not a runway mentioned inside a taxiway closure.
    # A second clause or subject can change the scope; retain as unresolved.
    match = SUBJECT.search(text)
    if not match:
        result["reason"] = "NO_EXPLICIT_RUNWAY_SUBJECT"
        return result
    before = text[:match.start()]
    tail = match[2]
    ids = [normalize(x) for x in re.findall(RUNWAY, match[1])]
    known = {end["id"] for end in inventory}
    if not set(ids) <= known:
        result["reason"] = "UNKNOWN_RUNWAY"
        return result
    pairs = {end["id"]: end["pair"].split("/") for end in inventory}
    result["runway_ends"] = sorted({end for id_ in ids for end in pairs[id_]})
    if "/" in match[1] and (len(ids) != 2 or set(ids) != set(pairs[ids[0]])):
        result.update(runway_ends=[], reason="INCONSISTENT_RUNWAY_PAIR")
        return result
    airport = re.escape(record["airport"][1:])
    envelope = rf"(?:!{airport}\s+\d{{2}}/\d{{3,6}}\s+)?(?:K?{airport}\s+)?"
    if not re.fullmatch(envelope, before):
        result["reason"] = "UNVERIFIED_SUBJECT_SCOPE"
        return result
    uncertain = re.search(r"\b(EXC|EXCEPT|PPR|PRIOR|PERMISSION|WHEN|IF|UNLESS|MAY|POSS|SUBJ|NOT|CANCEL|DAILY|DLY|MON|TUE|WED|THU|FRI|SAT|SUN|SR|SS)\b", text)
    if record.get("schedule") or uncertain or record["end_kind"] == "estimated":
        result["reason"] = "CONDITIONAL_OR_SCHEDULE_UNVERIFIED"
        return result
    # Accept only a whole-runway closure directly after the exact subject.
    # End-anchoring rejects exceptions, partial closures, and additional clauses.
    if not re.search(r"\b(TWY|TAXIWAY)\b", before) and re.fullmatch(r"(?:CLSD|CLOSED)[.]?(?:\s+\d{10}-\d{10}(?:EST)?)?", tail):
        result["runway_ends"] = sorted({end for id_ in ids for end in pairs[id_]})
        result.update(classification="closure", reason="EXPLICIT_WHOLE_RUNWAY_CLOSURE")
    elif re.fullmatch(r"(?:CLSD|CLOSED)\s+(?:TO\s+)?(?:LDG|LANDING|TKOF|TAKEOFF|DEPARTURES|ARRIVALS)[.]?", tail):
        result.update(classification="restriction", runway_ends=sorted(set(ids)), reason="DIRECTION_SPECIFIC_RESTRICTION")
    elif re.fullmatch(r"(?:PAPI|VASI|HIRL|REIL|RCLL|TDZL|ILS|ALS)\s+(?:OTS|U/S|UNSERVICEABLE)[.]?", tail):
        result.update(classification="restriction", runway_ends=sorted(set(ids)), reason="RUNWAY_EQUIPMENT_RESTRICTION")
    else:
        # An unparsed tail might name additional affected runways. Do not scope
        # that uncertainty to just the first subject and clear the others.
        result["runway_ends"] = []
    return result
