"""Offline reading-only adapter. No networking, acquisition, or lifecycle decisions."""
import argparse
import copy
import json
import re


def normalize(payload, *, airport, retrieved_utc, query_context):
    """Normalize an explicitly airport-scoped capture; retain each source feature.

    The caller supplies the query context and retrieval time from capture provenance.
    Grouping by that context does not establish a notice's operational applicability.
    """
    if not re.fullmatch(r"[A-Z0-9]{3,4}", airport):
        raise ValueError("airport must be an explicit dashboard airport code")
    if not isinstance(retrieved_utc, str) or not retrieved_utc.strip():
        raise ValueError("retrieval timestamp must be supplied from capture provenance")
    if not isinstance(query_context, dict) or not isinstance(query_context.get("location"), str):
        raise ValueError("explicit location query context is required; bulk/delta assignment is unsupported")
    location = query_context["location"].upper()
    if not re.fullmatch(r"[A-Z0-9]{3,4}", location):
        raise ValueError("location query context must contain a valid explicit location code")
    if airport != location and not (len(airport) == 4 and airport == "K" + location) and not (len(location) == 4 and location == "K" + airport):
        raise ValueError("airport and location must match, with only a US K-prefix alias supported")
    if not isinstance(payload, dict) or payload.get("status") != "Success":
        raise ValueError("response status is not Success; preserve the previous accepted snapshot")
    if payload.get("errors") not in (None, []):
        raise ValueError("response contains errors; preserve the previous accepted snapshot")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("geojson"), list):
        raise ValueError("inline data.geojson array required; content references are not fetched")
    notices = []
    for index, feature in enumerate(data["geojson"]):
        warnings = []
        core = feature.get("properties", {}).get("coreNOTAMData") if isinstance(feature, dict) and isinstance(feature.get("properties"), dict) else None
        core = core if isinstance(core, dict) else {}
        record = core.get("notam")
        if not isinstance(record, dict):
            warnings.append("NOTAM object missing or malformed")
            record = {}
        notice = {"source_index": index, "original_feature": copy.deepcopy(feature),
                  "source": "NMS service response; field semantics unverified",
                  "retrieved_utc": retrieved_utc, "formatted_texts": [], "warnings": warnings}
        for name in ("id", "number", "text", "issued", "effectiveStart", "effectiveEnd", "lastUpdated"):
            value = record.get(name)
            notice[name] = value if isinstance(value, str) else None
            if not isinstance(value, str) or not value.strip():
                warnings.append(name + " not supplied as a nonempty string; raw value retained in original_feature")
        translations = core.get("notamTranslation")
        if translations is not None and not isinstance(translations, list):
            warnings.append("notamTranslation is not an array; raw value retained")
        for position, translation in enumerate(translations if isinstance(translations, list) else []):
            if not isinstance(translation, dict):
                warnings.append("malformed translation at index " + str(position))
                continue
            for field in ("simpleText", "formattedText"):
                value = translation.get(field)
                if isinstance(value, str) and value.strip():
                    notice["formatted_texts"].append({"type": translation.get("type") if isinstance(translation.get("type"), str) else "Not supplied",
                                                    "field": field, "source_index": position, "text": value})
        # Preserve source fields verbatim without assigning lifecycle meanings or scope.
        notice["reported_relationships"] = {key: copy.deepcopy(record[key]) for key in ("type", "cancelationDate") if key in record}
        notices.append(notice)
    warning = "Documentation-derived mapping; real response validation outstanding. Query-scoped results; completeness unknown."
    return {"retrieved_utc": retrieved_utc, "query_context": copy.deepcopy(query_context),
            "original_response": copy.deepcopy(payload), "airports": {airport: {
                "notices": notices, "completeness": "UNKNOWN", "warning": warning}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="local JSON capture; never a URL")
    parser.add_argument("--airport", required=True)
    parser.add_argument("--location", required=True, help="actual location filter from capture provenance")
    parser.add_argument("--retrieved-utc", required=True, help="actual retrieval timestamp; preserved verbatim")
    args = parser.parse_args()
    with open(args.capture, encoding="utf-8") as stream:
        payload = json.load(stream)
    try:
        result = normalize(payload, airport=args.airport, retrieved_utc=args.retrieved_utc,
                           query_context={"location": args.location})
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
