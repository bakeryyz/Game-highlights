import json
import re
import unicodedata
from pathlib import Path

import statsapi

CROSSWALK_FILE = Path("data/crosswalk.json")


def _load_cache() -> dict:
    try:
        if CROSSWALK_FILE.exists():
            return json.loads(CROSSWALK_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        CROSSWALK_FILE.parent.mkdir(parents=True, exist_ok=True)
        CROSSWALK_FILE.write_text(json.dumps(data))
    except Exception:
        pass


def normalize_name(name: str) -> str:
    """
    Strip accents, drop jr/sr/ii/iii suffixes, normalize A.J.-style initials.
    "Ronald Acuña Jr." → "Ronald Acuna"
    "A.J. Pollock"     → "aj Pollock"
    "José Ramírez"     → "Jose Ramirez"
    """
    # Strip combining characters (accents)
    nfkd = unicodedata.normalize('NFKD', name)
    cleaned = ''.join(c for c in nfkd if not unicodedata.combining(c))
    # Normalize A.J. → aj  (two consecutive single-letter initials with dots)
    cleaned = re.sub(
        r'\b([A-Za-z])\.([A-Za-z])\.',
        lambda m: m.group(1).lower() + m.group(2).lower(),
        cleaned,
    )
    # Drop suffixes (including trailing periods)
    cleaned = re.sub(r'[,\s]+(jr\.?|sr\.?|ii|iii|iv)\.?(?=\s|$)', '', cleaned, flags=re.IGNORECASE)
    # Strip any lone trailing periods left over
    cleaned = re.sub(r'\s+\.\s*$', '', cleaned).strip(' .')
    return ' '.join(cleaned.split())


def resolve_mlbam_id(
    name: str,
    yahoo_id: int | str | None = None,
    pro_team: str | None = None,
) -> int | None:
    """
    Resolve a player to an MLBAM id using two strategies:
    1. baseball_id.Lookup.from_yahoo_ids — most reliable when yahoo_id is known
    2. statsapi.lookup_player by normalized name — fallback

    Results are cached to data/crosswalk.json. Returns None if unresolvable.
    Never guesses — returns None rather than a wrong id.
    """
    cache_key = f"yahoo:{yahoo_id}" if yahoo_id else f"name:{normalize_name(name)}"
    disk = _load_cache()
    if cache_key in disk:
        return disk[cache_key] or None

    result: int | None = None

    # Strategy 1: baseball_id library (yahoo_id → mlb_id)
    if yahoo_id is not None:
        try:
            from baseball_id import Lookup
            df = Lookup.from_yahoo_ids([int(yahoo_id)])
            if not df.empty and 'mlb_id' in df.columns:
                val = df['mlb_id'].iloc[0]
                if val and str(val) not in ('', 'nan', 'None'):
                    result = int(val)
        except Exception:
            pass

    # Strategy 2: statsapi name lookup
    if result is None:
        try:
            norm = normalize_name(name)
            hits = statsapi.lookup_player(norm)
            if not hits:
                # Try with original name
                hits = statsapi.lookup_player(name)
            if len(hits) == 1:
                result = int(hits[0]['id'])
            elif len(hits) > 1 and pro_team:
                # Disambiguate by pro team abbreviation
                abbr = pro_team.upper()
                for h in hits:
                    if h.get('currentTeam', {}).get('abbreviation', '').upper() == abbr:
                        result = int(h['id'])
                        break
            # If multiple hits and can't disambiguate → None (never guess)
        except Exception:
            pass

    # Cache the result (store None as False-y sentinel so we don't re-query)
    disk[cache_key] = result
    _save_cache(disk)
    return result
