import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

VEREINS_ID = 869
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(OUT_DIR, "locations_cache.json")

VTIMEZONE_BERLIN = [
    "BEGIN:VTIMEZONE",
    "TZID:Europe/Berlin",
    "X-LIC-LOCATION:Europe/Berlin",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:+0100",
    "TZOFFSETTO:+0200",
    "TZNAME:CEST",
    "DTSTART:19700329T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:+0200",
    "TZOFFSETTO:+0100",
    "TZNAME:CET",
    "DTSTART:19701025T030000",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
]


def fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; TVB-Kalender-Generator/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_matches():
    all_matches = {}
    club_url = f"https://www.basketball-bund.net/rest/club/id/{VEREINS_ID}/actualmatches?justHome=false&rangeDays=150"
    club_data = fetch_json(club_url)["data"]
    team_ids = set()
    for m in club_data.get("matches", []):
        all_matches[m["matchId"]] = m
        if m["homeTeam"]["clubId"] == VEREINS_ID:
            team_ids.add(m["homeTeam"]["teamPermanentId"])
        if m["guestTeam"]["clubId"] == VEREINS_ID:
            team_ids.add(m["guestTeam"]["teamPermanentId"])

    for tid in sorted(team_ids):
        if not tid:
            continue
        t_url = f"https://www.basketball-bund.net/rest/team/id/{tid}/matches"
        t_data = fetch_json(t_url)["data"]
        for m in t_data.get("matches", []):
            all_matches[m["matchId"]] = m
    return all_matches


def load_locations(match_ids):
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    missing = [str(mid) for mid in match_ids if str(mid) not in cache]

    def fetch_loc(mid_str):
        try:
            url = f"https://www.basketball-bund.net/rest/match/id/{mid_str}/matchInfo"
            data = fetch_json(url).get("data") or {}
            info = data.get("matchInfo") or {}
            feld = info.get("spielfeld")
            if feld:
                parts = [
                    feld.get("bezeichnung") or "",
                    feld.get("strasse") or "",
                    f"{feld.get('plz') or ''} {feld.get('ort') or ''}".strip(),
                ]
                loc = ", ".join(p for p in parts if p)
                return mid_str, loc
        except Exception:
            pass
        return mid_str, ""

    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for mid_str, loc in pool.map(fetch_loc, missing):
                if loc:
                    cache[mid_str] = loc
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)

    return cache


def escape_ics(text):
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def build_ics(matches_dict, locations, home_only=False, cal_name="TV Viktoria Blatzheim 1920"):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TV Blatzheim Basketball//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{cal_name}",
        "X-WR-TIMEZONE:Europe/Berlin",
        *VTIMEZONE_BERLIN,
    ]
    count = 0
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    sorted_matches = sorted(
        matches_dict.items(),
        key=lambda x: (x[1].get("kickoffDate") or "", x[1].get("kickoffTime") or ""),
    )

    for match_id, m in sorted_matches:
        if (
            not m.get("kickoffDate")
            or m.get("abgesagt")
            or m.get("verzicht")
            or m.get("kickoffTime") == "00:00"
        ):
            continue
        if home_only and m["homeTeam"]["clubId"] != VEREINS_ID:
            continue

        kickoff_date = m["kickoffDate"]
        kickoff_time = m.get("kickoffTime") or "12:00"
        start_dt = datetime.strptime(f"{kickoff_date} {kickoff_time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(hours=2)

        liga_data = m.get("ligaData") or {}
        ak = liga_data.get("akName") or ""
        label = f"[{ak}] " if ak else ""
        res = f" ({m['result']})" if m.get("result") else ""
        summary = f"{label}{m['homeTeam']['teamname']} vs. {m['guestTeam']['teamname']}{res}"
        liga = liga_data.get("liganame") or ""
        desc = (
            f"Liga: {liga}\n"
            f"Spieltag: {m.get('matchDay')} | Spiel-Nr.: {m.get('matchNo')}\n"
            f"Link: https://www.basketball-bund.net/static/#/spiel/{match_id}"
        )
        location = locations.get(str(match_id), "")

        event_lines = [
            "BEGIN:VEVENT",
            f"UID:dbb-match-{match_id}@basketball-bund.net",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;TZID=Europe/Berlin:{start_dt.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Europe/Berlin:{end_dt.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{escape_ics(summary)}",
            f"DESCRIPTION:{escape_ics(desc)}",
        ]
        if location:
            event_lines.append(f"LOCATION:{escape_ics(location)}")
        event_lines.append("END:VEVENT")
        lines.extend(event_lines)
        count += 1

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n", count


if __name__ == "__main__":
    matches = load_matches()
    locations = load_locations(matches.keys())

    ics_all, c_all = build_ics(
        matches, locations, home_only=False, cal_name="TV Blatzheim - Alle Spiele"
    )
    all_path = os.path.join(OUT_DIR, "tv_blatzheim_869_alle_spiele.ics")
    with open(all_path, "w", encoding="utf-8") as f:
        f.write(ics_all)

    ics_home, c_home = build_ics(
        matches, locations, home_only=True, cal_name="TV Blatzheim - Heimspiele"
    )
    home_path = os.path.join(OUT_DIR, "tv_blatzheim_869_heimspiele.ics")
    with open(home_path, "w", encoding="utf-8") as f:
        f.write(ics_home)

    print(f"Erstellt: {c_all} Spiele gesamt ({all_path})")
    print(f"Erstellt: {c_home} Heimspiele ({home_path})")
