"""
NHL Score Retriever - Today Only (clean rebuild)
Provides:
- display_todays_games(): prints today's games with scoring
- get_score_output(): captures printed output for Django

Features added per user request:
- Always fetch standings and show record + rank + points + streak in headers
- Show shots and goalie line under the score when available
- Colorize winners/live leading team in terminal
"""

import requests
import json
from datetime import datetime, timezone, timedelta
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import time
import pickle
from pathlib import Path
import threading

# When True, force ANSI color codes even if stdout.isatty() is False
FORCE_COLOR = False

# Persistent cache directory
CACHE_DIR = Path(__file__).parent / 'cache_data'
CACHE_DIR.mkdir(exist_ok=True)

# Simple in-memory cache
game_cache = {}
standings_cache = {'data': None, 'timestamp': None}
leaders_cache = {'data': None, 'timestamp': None}
schedule_cache = {}  # {date_str: {'games': [...], 'timestamp': datetime}}
CACHE_DURATION = 300  # 5 minutes in seconds

# Persistent cache file paths
GAME_CACHE_FILE = CACHE_DIR / 'game_cache.pkl'
STANDINGS_CACHE_FILE = CACHE_DIR / 'standings_cache.pkl'
LEADERS_CACHE_FILE = CACHE_DIR / 'leaders_cache.pkl'
SCHEDULE_CACHE_FILE = CACHE_DIR / 'schedule_cache.pkl'

def load_persistent_cache():
    """Load cached data from disk on startup."""
    global game_cache, standings_cache, leaders_cache, schedule_cache
    
    try:
        if GAME_CACHE_FILE.exists():
            with open(GAME_CACHE_FILE, 'rb') as f:
                game_cache = pickle.load(f)
            sys.stderr.write(f"[CACHE] Loaded {len(game_cache)} games from disk cache\n")
    except Exception as e:
        sys.stderr.write(f"[CACHE] Failed to load game cache: {e}\n")
    
    try:
        if STANDINGS_CACHE_FILE.exists():
            with open(STANDINGS_CACHE_FILE, 'rb') as f:
                standings_cache = pickle.load(f)
            if standings_cache.get('timestamp'):
                age = (datetime.now() - standings_cache['timestamp']).total_seconds()
                sys.stderr.write(f"[CACHE] Loaded standings from disk (age: {age:.0f}s)\n")
    except Exception as e:
        sys.stderr.write(f"[CACHE] Failed to load standings cache: {e}\n")
    
    try:
        if LEADERS_CACHE_FILE.exists():
            with open(LEADERS_CACHE_FILE, 'rb') as f:
                leaders_cache = pickle.load(f)
            if leaders_cache.get('timestamp'):
                age = (datetime.now() - leaders_cache['timestamp']).total_seconds()
                sys.stderr.write(f"[CACHE] Loaded leaders from disk (age: {age:.0f}s)\n")
    except Exception as e:
        sys.stderr.write(f"[CACHE] Failed to load leaders cache: {e}\n")
    
    try:
        if SCHEDULE_CACHE_FILE.exists():
            with open(SCHEDULE_CACHE_FILE, 'rb') as f:
                schedule_cache = pickle.load(f)
            sys.stderr.write(f"[CACHE] Loaded schedule for {len(schedule_cache)} dates from disk cache\n")
    except Exception as e:
        sys.stderr.write(f"[CACHE] Failed to load schedule cache: {e}\n")

def save_game_cache():
    """Save game cache to disk."""
    try:
        with open(GAME_CACHE_FILE, 'wb') as f:
            pickle.dump(game_cache, f)
    except Exception as e:
        sys.stderr.write(f"[CACHE] Failed to save game cache: {e}\n")

def save_standings_cache():
    """Save standings cache to disk."""
    try:
        with open(STANDINGS_CACHE_FILE, 'wb') as f:
            pickle.dump(standings_cache, f)
    except Exception as e:
        sys.stderr.write(f"[CACHE] Failed to save standings cache: {e}\n")

def save_leaders_cache():
    """Save leaders cache to disk."""
    try:
        with open(LEADERS_CACHE_FILE, 'wb') as f:
            pickle.dump(leaders_cache, f)
    except Exception as e:
        sys.stderr.write(f"[CACHE] Failed to save leaders cache: {e}\n")

def save_schedule_cache():
    """Save schedule cache to disk."""
    try:
        with open(SCHEDULE_CACHE_FILE, 'wb') as f:
            pickle.dump(schedule_cache, f)
    except Exception as e:
        sys.stderr.write(f"[CACHE] Failed to save schedule cache: {e}\n")

# Load cache on module import
load_persistent_cache()


def prefetch_dates_range(center_date_str, days_before=2, days_after=2):
    """Prefetch game data for a range of dates around center_date.
    This loads schedule + game data into cache without blocking.
    """
    try:
        center_date = datetime.strptime(center_date_str, '%Y-%m-%d').date()
    except Exception:
        center_date = datetime.now().date()
    
    dates_to_fetch = []
    for offset in range(-days_before, days_after + 1):
        date = center_date + timedelta(days=offset)
        dates_to_fetch.append(date.strftime('%Y-%m-%d'))
    
    sys.stderr.write(f"[PREFETCH] Prefetching {len(dates_to_fetch)} dates: {dates_to_fetch[0]} to {dates_to_fetch[-1]}\n")
    prefetch_start = time.time()
    
    # Fetch all schedules first (fast with caching)
    all_game_ids = []
    for date_str in dates_to_fetch:
        games = get_nhl_games_for_date(date_str)
        game_ids = [g.get('id') for g in games if g.get('id')]
        all_game_ids.extend(game_ids)
    
    # Fetch all game data in batch (uses cache, only fetches missing)
    if all_game_ids:
        fetch_game_data_batch(all_game_ids, fetch_all=True)
    
    prefetch_time = (time.time() - prefetch_start) * 1000
    sys.stderr.write(f"[PREFETCH] Completed prefetching {len(dates_to_fetch)} dates with {len(all_game_ids)} games in {prefetch_time:.0f}ms\n")
    sys.stderr.flush()


def prefetch_adjacent_dates(date_str, days=1):
    """Prefetch dates adjacent to the given date (for carousel navigation).
    Runs in background thread to not block response.
    """
    def _prefetch():
        try:
            prefetch_dates_range(date_str, days_before=days, days_after=days)
        except Exception as e:
            sys.stderr.write(f"[PREFETCH] Background prefetch failed: {e}\n")
    
    thread = threading.Thread(target=_prefetch, daemon=True)
    thread.start()


def preload_startup_data():
    """Preload today ±2 days on server startup for instant carousel navigation."""
    sys.stderr.write("[PRELOADER] Starting initial data prefetch...\n")
    today = datetime.now().strftime('%Y-%m-%d')
    prefetch_dates_range(today, days_before=2, days_after=2)
    sys.stderr.write("[PRELOADER] Initial prefetch complete!\n")


def get_team_logo_path(team_abbrev):
    """Get the local path for a team's logo. All logos are stored locally in static/team_logos/"""
    if not team_abbrev:
        return ''
    return f'/static/team_logos/{team_abbrev}.svg'


def fetch_game_data_batch(game_ids, fetch_all=True):
    """
    Fetch game data for multiple games concurrently.
    If fetch_all=False, only fetches essential data (faster for scheduled games).
    """
    batch_start = time.time()
    sys.stderr.write(f"[BATCH] Fetching data for {len(game_ids)} games (fetch_all={fetch_all})...\n")
    game_data = {}

    def fetch_single(game_id):
        fetch_start = time.time()
        if game_id in game_cache:
            sys.stderr.write(f"[CACHE] Game {game_id} found in cache\n")
            return game_id, game_cache[game_id]
        
        # Try landing endpoint first (has most data)
        url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/landing"
        try:
            api_start = time.time()
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            data = r.json()
            api_time = (time.time() - api_start) * 1000
            sys.stderr.write(f"[API] Game {game_id} landing endpoint: {api_time:.0f}ms\n")
            game_cache[game_id] = data
            save_game_cache()  # Persist to disk
            return game_id, data
        except Exception as e:
            sys.stderr.write(f"[API] Game {game_id} landing failed, trying boxscore...\n")
            # Fallback to boxscore if landing fails
            if fetch_all:
                try:
                    url = f"https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"
                    api_start = time.time()
                    r = requests.get(url, timeout=5)
                    r.raise_for_status()
                    data = r.json()
                    api_time = (time.time() - api_start) * 1000
                    sys.stderr.write(f"[API] Game {game_id} boxscore endpoint: {api_time:.0f}ms\n")
                    game_cache[game_id] = data
                    save_game_cache()  # Persist to disk
                    return game_id, data
                except Exception as e2:
                    sys.stderr.write(f"[ERROR] Game {game_id} both endpoints failed\n")
                    pass
        return game_id, None

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_single, gid): gid for gid in game_ids}
        for fut in as_completed(futures):
            gid, data = fut.result()
            if data:
                game_data[gid] = data
    
    batch_time = (time.time() - batch_start) * 1000
    sys.stderr.write(f"[BATCH] Completed fetching {len(game_data)}/{len(game_ids)} games in {batch_time:.0f}ms\n")
    sys.stderr.flush()
    return game_data


def get_nhl_games_for_date(date_str):
    """Fetch schedule for a specific date string `YYYY-MM-DD`.
    Returns list of games or empty list on error.
    Cached for 5 minutes to improve performance.
    """
    if not date_str:
        return []
    # ensure we have a YYYY-MM-DD string
    try:
        # allow passing datetime.date or datetime
        if hasattr(date_str, 'strftime'):
            date_val = date_str.strftime('%Y-%m-%d')
        else:
            date_val = str(date_str)
        # basic validation
        datetime.strptime(date_val, '%Y-%m-%d')
    except Exception:
        return []
    
    # Check cache first
    if date_val in schedule_cache:
        cached = schedule_cache[date_val]
        if cached.get('timestamp'):
            elapsed = (datetime.now() - cached['timestamp']).total_seconds()
            if elapsed < CACHE_DURATION:
                sys.stderr.write(f"[CACHE] Using cached schedule for {date_val} (age: {elapsed:.0f}s)\n")
                return cached.get('games', [])
    
    sys.stderr.write(f"[SCHEDULE] Cache miss, fetching schedule for date: {date_val}\n")
    url = f"https://api-web.nhle.com/v1/schedule/{date_val}"
    try:
        api_start = time.time()
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        api_time = (time.time() - api_start) * 1000
        games = []
        for wd in data.get('gameWeek', []):
            if wd.get('date') == date_val and 'games' in wd:
                games.extend(wd['games'])
        sys.stderr.write(f"[API] Schedule API call: {api_time:.0f}ms, found {len(games)} games\n")
        
        # Cache the result
        schedule_cache[date_val] = {'games': games, 'timestamp': datetime.now()}
        save_schedule_cache()  # Persist to disk
        
        sys.stderr.flush()
        return games
    except Exception as e:
        sys.stderr.write(f"[ERROR] Schedule API failed: {e}\n")
        return []

def get_todays_nhl_games():
    return get_nhl_games_for_date(datetime.now().strftime('%Y-%m-%d'))


def trigger_prefetch_for_date(date_str):
    """Trigger background prefetch of adjacent dates for smooth carousel navigation."""
    # Prefetch ±1 day from requested date (runs in background)
    prefetch_adjacent_dates(date_str, days=1)


def get_game_boxscore(game_id):
    # prefer landing
    for url in (f"https://api-web.nhle.com/v1/gamecenter/{game_id}/landing",
                f"https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"):
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return None


def format_scoring_summary(game_id, game_data=None):
    if game_data is None:
        game_data = get_game_boxscore(game_id)
    if not game_data or not isinstance(game_data, dict):
        return []

    goals_out = []

    # Helper to normalize period name
    def _period_name(period_type, period_num):
        if not period_type:
            return f"Period {period_num}"
        pt = str(period_type).upper()
        if pt == 'REG' or pt == 'R':
            return f"Period {period_num}"
        if pt == 'OT':
            return 'OT' if int(period_num) == 4 else f"OT{int(period_num)-3}"
        if pt == 'SO':
            return 'SO'
        return f"Period {period_num}"

    # 1) Preferred: summary.scoring -> periods -> goals
    summary = None
    if isinstance(game_data.get('summary'), dict):
        summary = game_data['summary']
    elif isinstance(game_data.get('boxscore'), dict):
        summary = game_data['boxscore'].get('summary')

    if summary and isinstance(summary, dict) and 'scoring' in summary:
        scoring_periods = summary.get('scoring', [])
        sys.stderr.write(f"[SCORING] Found {len(scoring_periods)} periods in summary.scoring\n")
        for period_data in scoring_periods:
            pd = period_data.get('periodDescriptor') or {}
            pnum = pd.get('number') or pd.get('period') or 0
            ptype = pd.get('periodType') or pd.get('type') or 'REG'
            pname = _period_name(ptype, pnum)
            period_goals = period_data.get('goals', []) or []
            sys.stderr.write(f"[SCORING] Period {pname}: {len(period_goals)} goals\n")
            for g in period_goals:
                # scorer
                def _name_of(x):
                    if not x:
                        return ''
                    if isinstance(x, dict):
                        # sometimes the structure is {'player': {...}} or player dict itself
                        p = x.get('player') or x
                        # prefer fullName
                        full = p.get('fullName') or p.get('fullName')
                        if isinstance(full, str) and full:
                            return full
                        # try nested 'default' values used in some payloads
                        fn = p.get('firstName')
                        ln = p.get('lastName')
                        if isinstance(fn, dict):
                            fn = fn.get('default', '')
                        if isinstance(ln, dict):
                            ln = ln.get('default', '')
                        fn = fn or ''
                        ln = ln or ''
                        name = f"{fn} {ln}".strip()
                        if name:
                            return name
                        # fall back to any name-like fields
                        return str(p.get('id') or '')
                    return str(x)

                # Scorer can appear in several shapes; try common ones
                def _extract_scorer_from_goal(gobj):
                    # direct firstName/lastName fields
                    fn = gobj.get('firstName') if 'firstName' in gobj else None
                    ln = gobj.get('lastName') if 'lastName' in gobj else None
                    def _norm_name_part(x):
                        if isinstance(x, dict):
                            return x.get('default') or x.get('value') or ''
                        return x or ''
                    if fn is not None or ln is not None:
                        fnv = _norm_name_part(fn)
                        lnv = _norm_name_part(ln)
                        name = f"{fnv} {lnv}".strip()
                        if name:
                            return name

                    # nested 'scorer' or 'player' dicts
                    for key in ('scorer', 'scorerPlayer', 'player', 'scoringPlayer'):
                        val = gobj.get(key)
                        if val:
                            nm = _name_of(val)
                            if nm:
                                return nm

                    # fallback: maybe 'playerName' or 'playerFullName'
                    for key in ('playerName', 'playerFullName', 'name'):
                        val = gobj.get(key)
                        if isinstance(val, str) and val:
                            return val

                    return ''

                try:
                    scorer = _extract_scorer_from_goal(g)
                    # try to extract an id for the scorer from several possible shapes
                    scorer_id = None
                    try:
                        if isinstance(g.get('scorer'), dict):
                            scorer_id = g.get('scorer').get('id') or g.get('scorer').get('playerId')
                        if not scorer_id and isinstance(g.get('player'), dict):
                            scorer_id = g.get('player').get('id')
                        # some payloads put 'scorerPlayer'
                        if not scorer_id and isinstance(g.get('scorerPlayer'), dict):
                            scorer_id = g.get('scorerPlayer').get('id')
                    except Exception:
                        scorer_id = None

                    scorer_goals = g.get('goalsToDate') or g.get('scorerGoals') or g.get('seasonGoals') or 0
                    assists = []
                    for a in g.get('assists', []) or []:
                        # Try to include season assist totals when available and extract nested names
                        # For assists keep structured data with player id when available
                        aid = None
                        aname = None
                        atot = None
                        try:
                            if isinstance(a, dict):
                                # player wrapping
                                p = a.get('player') if 'player' in a else a
                                if isinstance(p, dict):
                                    aid = p.get('id') or p.get('playerId')
                                    # Extract name - handle nested 'default' structure
                                    fn = p.get('firstName', '')
                                    ln = p.get('lastName', '')
                                    # Check if firstName/lastName are dicts with 'default' key
                                    if isinstance(fn, dict):
                                        fn = fn.get('default', '')
                                    if isinstance(ln, dict):
                                        ln = ln.get('default', '')
                                    aname = p.get('fullName') or f"{fn} {ln}".strip()
                                    # If fullName is a dict, extract default
                                    if isinstance(aname, dict):
                                        aname = aname.get('default', '')
                                # assist totals
                                for k in ('assistsToDate','seasonAssists','seasonTotal','assists'):
                                    if k in a:
                                        atot = a.get(k)
                                        break
                                if atot is None and isinstance(p, dict):
                                    for k in ('assistsToDate','seasonAssists','seasonTotal','assists'):
                                        if k in p:
                                            atot = p.get(k)
                                            break
                            elif isinstance(a, str):
                                aname = a
                        except Exception:
                            pass

                        display = aname or str(a)
                        if atot is not None and display and not display.startswith('{'):
                            try:
                                display = f"{display} ({int(atot)})"
                            except Exception:
                                display = f"{display} ({atot})"
                        # Ensure we never return a dict as display - convert to string if needed
                        if isinstance(display, dict):
                            display = str(display.get('name', display.get('default', 'Unknown')))
                        assists.append({'id': aid, 'name': aname, 'display': str(display)})
                    def _norm_abbrev(x):
                        if not x:
                            return ''
                        if isinstance(x, dict):
                            return x.get('default') or x.get('triCode') or x.get('abbrev') or x.get('shortName') or ''
                        return str(x)

                    team_abbrev = _norm_abbrev(g.get('teamAbbrev') or (g.get('team') or {}).get('abbrev') or (g.get('team') or {}))
                    scorer_display = f"{scorer} ({scorer_goals})" if scorer else f"({scorer_goals})"
                    goals_out.append({'period': pname, 'time': g.get('timeInPeriod') or g.get('time') or '', 'team': team_abbrev, 'scorer': {'id': scorer_id, 'name': scorer, 'display': scorer_display}, 'assists': assists})
                except Exception as e:
                    sys.stderr.write(f"[ERROR] Failed to process goal in {pname}: {e}\n")
                    continue
        sys.stderr.write(f"[SCORING] Total goals extracted: {len(goals_out)}\n")
        if goals_out:
            return goals_out

    # 2) Fallback: play-by-play in allPlays or plays -> filter GOAL events
    all_plays = None
    if 'allPlays' in game_data:
        all_plays = game_data.get('allPlays')
    elif 'plays' in game_data and isinstance(game_data['plays'], dict):
        all_plays = game_data['plays'].get('allPlays') or game_data['plays'].get('plays')

    if isinstance(all_plays, list):
        for play in all_plays:
            result = play.get('result') or {}
            event = (result.get('eventTypeId') or result.get('event') or '').upper()
            if 'GOAL' in event:
                about = play.get('about') or {}
                pnum = about.get('period') or about.get('periodNumber') or 0
                ptype = about.get('periodType') or about.get('periodType') or 'REG'
                pname = _period_name(ptype, pnum)
                time_in_period = about.get('periodTime') or about.get('periodTimeRemaining') or ''
                team = (play.get('team') or {})
                team = (team.get('triCode') or team.get('abbrev') or team.get('name') or team.get('shortName') or team)
                if isinstance(team, dict):
                    team = team.get('default') or team.get('triCode') or team.get('abbrev') or ''
                # players list
                players = result.get('players') or []
                scorer = ''
                scorer_id = None
                assists = []
                scorer_goals = 0
                for p in players:
                    ptype = p.get('playerType') or p.get('type') or ''
                    player = p.get('player') or {}
                    name = player.get('fullName') or (player.get('firstName','') + ' ' + player.get('lastName','')).strip()
                    pid = player.get('id') or p.get('playerId') or None
                    if 'SCORER' in str(ptype).upper() or 'GOAL' in str(ptype).upper():
                        scorer = name
                        scorer_id = pid
                        scorer_goals = p.get('seasonTotal') or p.get('goalsToDate') or 0
                    else:
                        if name:
                            atot = p.get('seasonTotal') or p.get('assistsToDate') or p.get('assists') or None
                            display = name
                            if atot is not None:
                                try:
                                    display = f"{name} ({int(atot)})"
                                except Exception:
                                    display = f"{name} ({atot})"
                            assists.append({'id': pid, 'name': name, 'display': display})
                goals_out.append({'period': pname, 'time': time_in_period, 'team': team, 'scorer': {'id': scorer_id, 'name': scorer, 'display': f"{scorer} ({scorer_goals})"}, 'assists': assists})
        if goals_out:
            return goals_out

    # 3) scoringPlays list of indices into allPlays
    sp = game_data.get('scoringPlays')
    if isinstance(sp, list) and isinstance(all_plays, list):
        for idx in sp:
            try:
                play = all_plays[int(idx)]
            except Exception:
                continue
            result = play.get('result') or {}
            about = play.get('about') or {}
            pnum = about.get('period') or 0
            pname = _period_name(about.get('periodType') or 'REG', pnum)
            time_in_period = about.get('periodTime') or ''
            team = (play.get('team') or {})
            team = (team.get('triCode') or team.get('abbrev') or team.get('name') or team.get('shortName') or team)
            if isinstance(team, dict):
                team = team.get('default') or team.get('triCode') or team.get('abbrev') or ''
            players = result.get('players') or []
            scorer = ''
            scorer_id = None
            assists = []
            scorer_goals = 0
            for p in players:
                ptype = p.get('playerType') or p.get('type') or ''
                player = p.get('player') or {}
                name = player.get('fullName') or (player.get('firstName','') + ' ' + player.get('lastName','')).strip()
                pid = player.get('id') or p.get('playerId') or None
                if 'SCORER' in str(ptype).upper() or 'GOAL' in str(ptype).upper():
                    scorer = name
                    scorer_id = pid
                    scorer_goals = p.get('seasonTotal') or p.get('goalsToDate') or 0
                else:
                    if name:
                        atot = p.get('seasonTotal') or p.get('assistsToDate') or p.get('assists') or None
                        display = name
                        if atot is not None:
                            try:
                                display = f"{name} ({int(atot)})"
                            except Exception:
                                display = f"{name} ({atot})"
                        assists.append({'id': pid, 'name': name, 'display': display})
            goals_out.append({'period': pname, 'time': time_in_period, 'team': team, 'scorer': {'id': scorer_id, 'name': scorer, 'display': f"{scorer} ({scorer_goals})"}, 'assists': assists})
        if goals_out:
            return goals_out

    return []


def format_team_record(team_obj):
    """Return a string like '21-2-6' for a team object if available.
    Tries several possible field names and formats (dict or string). Returns
    an empty string if no record data is found.
    """
    if not team_obj:
        return ''

    # Try common nested record fields used by various NHL endpoints
    record = team_obj.get('record') or team_obj.get('leagueRecord') or team_obj.get('seasonRecord') or {}

    # If record is already a string like '21-2-6', return it
    if isinstance(record, str) and record.strip():
        return record.strip()

    # If record is a dict, try to extract wins/losses/overtime
    if isinstance(record, dict):
        # Only attempt to format if at least one of the common keys exists
        possible_keys = set(record.keys())
        known_keys = {'wins', 'w', 'losses', 'l', 'ot', 'overtimeLosses', 'otl', 'o'}
        if possible_keys & known_keys:
            w = record.get('wins') if 'wins' in record else record.get('w')
            l = record.get('losses') if 'losses' in record else record.get('l')
            # try a few different overtime keys
            o = None
            for k in ('ot', 'overtimeLosses', 'otl', 'o'):
                if k in record:
                    o = record.get(k)
                    break

            if w is not None and l is not None and o is not None:
                return f"{w}-{l}-{o}"

            # If not all three are present, don't fabricate zeros — return empty
            return ''

    # Fallback: some endpoints put wins/losses/ot at top-level of team object
    try:
        if any(k in team_obj for k in ('wins', 'w', 'losses', 'l', 'overtimeLosses', 'ot', 'otl')):
            w = team_obj.get('wins') if 'wins' in team_obj else team_obj.get('w')
            l = team_obj.get('losses') if 'losses' in team_obj else team_obj.get('l')
            o = None
            for k in ('overtimeLosses', 'ot', 'otl'):
                if k in team_obj:
                    o = team_obj.get(k)
                    break

            if w is not None and l is not None and o is not None:
                return f"{w}-{l}-{o}"
    except Exception:
        pass

    return ''


def _get_team_id(team_obj):
    """Try several common places to find a team id inside a team object."""
    if not team_obj or not isinstance(team_obj, dict):
        return None
    for key in ('id', 'teamId', 'team_id'):
        if key in team_obj and isinstance(team_obj.get(key), int):
            return team_obj.get(key)

    # Sometimes the team is nested under 'team' key
    nested = team_obj.get('team') if isinstance(team_obj.get('team'), dict) else None
    if nested:
        for key in ('id', 'teamId', 'team_id'):
            if key in nested and isinstance(nested.get(key), int):
                return nested.get(key)

    return None


def _color_text(text, color_name):
    """Return ANSI-colored text when stdout is a tty; otherwise return plain text."""
    if not (FORCE_COLOR or (sys.stdout and hasattr(sys.stdout, 'isatty') and sys.stdout.isatty())):
        return text

    codes = {
        'reset': '\u001b[0m',
        'red': '\u001b[31m',
        'green': '\u001b[32m',
        'yellow': '\u001b[33m',
        'blue': '\u001b[34m',
        'cyan': '\u001b[36m',
        'magenta': '\u001b[35m',
        'bold': '\u001b[1m',
    }
    code = codes.get(color_name, '')
    reset = codes['reset']
    return f"{code}{text}{reset}" if code else text


def _color_and_bold(text, color_name):
    """Combine color and bold ANSI codes when stdout is a tty."""
    if not (FORCE_COLOR or (sys.stdout and hasattr(sys.stdout, 'isatty') and sys.stdout.isatty())):
        return text
    codes = {
        'reset': '\u001b[0m',
        'red': '\u001b[31m',
        'green': '\u001b[32m',
        'yellow': '\u001b[33m',
        'blue': '\u001b[34m',
        'cyan': '\u001b[36m',
        'magenta': '\u001b[35m',
        'bold': '\u001b[1m',
    }
    color_code = codes.get(color_name, '')
    bold_code = codes.get('bold', '')
    reset = codes['reset']
    if color_code and bold_code:
        return f"{color_code}{bold_code}{text}{reset}"
    if color_code:
        return f"{color_code}{text}{reset}"
    if bold_code:
        return f"{bold_code}{text}{reset}"
    return text


def _apply_styles(text, color_name=None, bold=False, underline=False):
    """Apply color, bold, and/or underline ANSI styles when supported."""
    if not (FORCE_COLOR or (sys.stdout and hasattr(sys.stdout, 'isatty') and sys.stdout.isatty())):
        return text
    codes = {
        'reset': '\u001b[0m',
        'red': '\u001b[31m',
        'green': '\u001b[32m',
        'yellow': '\u001b[33m',
        'blue': '\u001b[34m',
        'cyan': '\u001b[36m',
        'magenta': '\u001b[35m',
        'bold': '\u001b[1m',
        'underline': '\u001b[4m',
    }
    parts = []
    if color_name:
        parts.append(codes.get(color_name, ''))
    if bold:
        parts.append(codes.get('bold', ''))
    if underline:
        parts.append(codes.get('underline', ''))
    prefix = ''.join(parts)
    reset = codes['reset']
    if prefix:
        return f"{prefix}{text}{reset}"
    return text


def _normalize_assist_item(a):
    """Return a normalized assist dict with keys: id, name, display.
    Handles multiple input shapes (raw API dicts or our own structured dicts).
    """
    if a is None:
        return {'id': None, 'name': '', 'display': ''}
    # If already normalized-ish (has 'display' and 'id'), but guard against
    # cases where 'display' itself is a stringified raw payload (e.g. "{'playerId':...}").
    if isinstance(a, dict) and ('display' in a and 'id' in a):
        disp = a.get('display') or a.get('name') or ''
        if isinstance(disp, str) and '{' in disp and 'playerId' in disp:
            try:
                import ast
                to_parse = disp
                if ('}, {' in to_parse) or ('},\n {' in to_parse) or ('}{' in to_parse):
                    to_parse = f"[{to_parse}]"
                parsed = ast.literal_eval(to_parse)
                if isinstance(parsed, list) and parsed:
                    a = parsed[0]
                else:
                    a = parsed
                # fall through to full dict handling below
            except Exception:
                return {'id': a.get('id'), 'name': a.get('name') or '', 'display': str(disp)}
        else:
            return {'id': a.get('id'), 'name': a.get('name') or '', 'display': str(disp)}
    # If it's a string representing a dict from some payloads, try to parse it
    if isinstance(a, str):
        s = a.strip()
        if s.startswith('{') and 'playerId' in s:
            try:
                import ast
                to_parse = s
                # if the string contains multiple dicts separated by comma, make it a list
                if ('}, {' in to_parse) or ('},\n {' in to_parse) or ('}{' in to_parse):
                    to_parse = f"[{to_parse}]"
                parsed = ast.literal_eval(to_parse)
                # if parsed is a list, take the first entry as this item
                if isinstance(parsed, list) and parsed:
                    a = parsed[0]
                else:
                    a = parsed
            except Exception:
                pass

    # If it's a dict from API raw payload
    def _name_val(x):
        if x is None:
            return ''
        if isinstance(x, dict):
            return x.get('default') or x.get('value') or x.get('fullName') or ''
        return str(x)

    if isinstance(a, dict):
        # Try common player wrapper
        pid = None
        pname = None
        atot = None
        # direct fields
        if 'playerId' in a and a.get('playerId'):
            pid = a.get('playerId')
        if 'id' in a and a.get('id') and not pid:
            pid = a.get('id')

        # Attempt to extract nested 'player' dict
        p = a.get('player') if isinstance(a.get('player'), dict) else a
        if isinstance(p, dict):
            # name possibilities, handle parts that may be dicts
            pname = p.get('fullName') or ''
            if not pname:
                fn = _name_val(p.get('firstName'))
                ln = _name_val(p.get('lastName'))
                pname = f"{fn} {ln}".strip() if (fn or ln) else ''
            if not pname:
                pname = _name_val(p.get('name')) or _name_val(p.get('default'))
            if not pid:
                pid = p.get('id') or p.get('playerId')

        # totals
        for k in ('assistsToDate', 'seasonAssists', 'seasonTotal', 'assists'):
            if k in a and a.get(k) is not None:
                atot = a.get(k)
                break
        if atot is None and isinstance(p, dict):
            for k in ('assistsToDate', 'seasonAssists', 'seasonTotal', 'assists'):
                if k in p and p.get(k) is not None:
                    atot = p.get(k)
                    break

        # Build display
        display = None
        if pname:
            display = str(pname)
        else:
            # try several fields for human-readable name
            display = _name_val(a.get('name')) or _name_val(a.get('player')) or _name_val(a.get('default'))
            if not display:
                try:
                    display = str(a)
                except Exception:
                    display = ''

        if atot is not None:
            try:
                display = f"{display} ({int(atot)})"
            except Exception:
                display = f"{display} ({atot})"

        return {'id': pid, 'name': pname or '', 'display': display}

    # Fallback for simple strings or other types
    return {'id': None, 'name': str(a), 'display': str(a)}


def _extract_shots_and_goalies(game_data):
    """Return dict with shots and goalie summaries for away/home when available.
    Returns: {'shots': (away_shots, home_shots), 'goalies': (away_goalie_str, home_goalie_str)}
    Missing values are None.
    """
    shots = (None, None)
    goalies = (None, None)

    if not game_data or not isinstance(game_data, dict):
        return {'shots': shots, 'goalies': goalies}

    # Try boxscore teamStats
    try:
        bs = game_data.get('boxscore') or {}
        # Several shapes: teamStats -> awayTeam/homeTeam or away/home
        team_stats = bs.get('teamStats') or bs.get('teams') or {}

        def _get_shots_from_teamstats(ts, side):
            if not ts:
                return None
            # Common keys
            for k in ('shotsOnGoal', 'shots'):
                if isinstance(ts.get(side, {}), dict) and k in ts.get(side, {}):
                    return ts.get(side, {}).get(k)
            # try teamSkaterStats nested
            try:
                return ts.get(side, {}).get('teamSkaterStats', {}).get('shotsOnGoal')
            except Exception:
                return None

        # Try 'away'/'home' or 'awayTeam'/'homeTeam'
        away_shots = _get_shots_from_teamstats(team_stats, 'away')
        home_shots = _get_shots_from_teamstats(team_stats, 'home')
        if away_shots is None and home_shots is None:
            away_shots = _get_shots_from_teamstats(team_stats, 'awayTeam')
            home_shots = _get_shots_from_teamstats(team_stats, 'homeTeam')

        shots = (away_shots, home_shots)
    except Exception:
        shots = (None, None)

    # Extract goalie stats from playerByGameStats if available
    try:
        pstats = None
        if 'playerByGameStats' in game_data:
            pstats = game_data['playerByGameStats']
        elif 'boxscore' in game_data and 'playerByGameStats' in game_data['boxscore']:
            pstats = game_data['boxscore']['playerByGameStats']

        def _format_goalie_list(team_stats):
            if not isinstance(team_stats, dict):
                return None
            # goalies list may be under 'goalies'
            for key in ('goalies', 'goaliesList'):
                gl = team_stats.get(key)
                if isinstance(gl, list) and gl:
                    # pick first goalie
                    g = gl[0]
                    fname = g.get('firstName', {})
                    lname = g.get('lastName', {})
                    # values might be dict with 'default'
                    if isinstance(fname, dict):
                        fname = fname.get('default', '')
                    if isinstance(lname, dict):
                        lname = lname.get('default', '')
                    name = f"{fname} {lname}".strip()
                    saves = g.get('saves') or g.get('savesToDate') or g.get('savesOnGoal')
                    ga = g.get('goalsAgainst') or g.get('goalsAllowed') or g.get('goalsAgainstToDate')
                    if saves is not None and ga is not None:
                        return f"{name} {saves}/{int(saves)+int(ga)}"
                    # fallback: show GA only
                    if ga is not None:
                        return f"{name} GA:{ga}"
                    return name
            return None

        if pstats:
            away_g = pstats.get('awayTeam') if isinstance(pstats.get('awayTeam'), dict) else pstats.get('away')
            home_g = pstats.get('homeTeam') if isinstance(pstats.get('homeTeam'), dict) else pstats.get('home')
            away_goalie = _format_goalie_list(away_g) if away_g else None
            home_goalie = _format_goalie_list(home_g) if home_g else None
            goalies = (away_goalie, home_goalie)
    except Exception:
        goalies = (None, None)

    return {'shots': shots, 'goalies': goalies}


def fetch_standings_records():
    """Fetch current standings and return two maps:
    - id_to_info: {team_id: {record, points, rank, streak}}
    - abbrev_to_info: {abbrev: {record, points, rank, streak}}

    Uses the public NHL statsapi endpoints. Returns empty maps on error.
    Cached for 5 minutes to improve performance.
    """
    global standings_cache
    
    # Check cache
    if standings_cache['data'] is not None and standings_cache['timestamp'] is not None:
        elapsed = (datetime.now() - standings_cache['timestamp']).total_seconds()
        if elapsed < CACHE_DURATION:
            sys.stderr.write(f"[CACHE] Using cached standings (age: {elapsed:.0f}s)\n")
            return standings_cache['data']
    
    sys.stderr.write(f"[STANDINGS] Cache miss, fetching fresh standings...\n")
    id_to_info = {}
    abbrev_to_info = {}
    id_to_abbrev = {}

    try:
        # First try the api-web 'now' standings endpoint which returns current standings by abbrev
        try:
            now_url = "https://api-web.nhle.com/v1/standings/now"
            api_start = time.time()
            rnow = requests.get(now_url, timeout=8)
            api_time = (time.time() - api_start) * 1000
            if rnow.status_code == 200:
                sys.stderr.write(f"[API] Standings 'now' endpoint: {api_time:.0f}ms\n")
                sjson = rnow.json()
                # sjson contains a list under 'standings'
                for entry in sjson.get('standings', []) or []:
                    # extract abbreviation
                    abbrev = None
                    ta = entry.get('teamAbbrev') or entry.get('teamAbbrev', {})
                    if isinstance(ta, dict):
                        abbrev = ta.get('default') or ta.get('triCode') or ta.get('abbrev')
                    elif isinstance(ta, str):
                        abbrev = ta

                    # wins/losses/ot variants
                    w = entry.get('wins') or entry.get('win') or 0
                    l = entry.get('losses') or entry.get('loss') or 0
                    ot = entry.get('otLosses') or entry.get('overtimeLosses') or entry.get('ot') or entry.get('shootoutLosses') or 0
                    record_str = f"{w}-{l}-{ot}"

                    points = entry.get('points')
                    streak = None
                    sc = entry.get('streakCode') or entry.get('streak')
                    if sc:
                        # combine with count if available
                        count = entry.get('streakCount')
                        streak = f"{sc}{count if count is not None else ''}"

                    rank = entry.get('divisionRank') or entry.get('conferenceRank') or entry.get('leagueRank')

                    info = {'record': record_str, 'points': points, 'rank': rank, 'streak': streak}
                    if abbrev:
                        # normalize abbrev to string
                        a = str(abbrev).strip()
                        abbrev_to_info[a] = info

                # Build normalized abbrev map (upper/lower/stripped)
                import re
                def _norm_keys(s):
                    keys = set()
                    if not s:
                        return keys
                    s0 = str(s).strip()
                    keys.add(s0)
                    keys.add(s0.upper())
                    keys.add(s0.lower())
                    s_alnum = re.sub(r'[^A-Za-z0-9]', '', s0)
                    if s_alnum:
                        keys.add(s_alnum)
                        keys.add(s_alnum.upper())
                    return keys

                norm_abbrev_map = {}
                for a, inf in abbrev_to_info.items():
                    for k in _norm_keys(a):
                        if k and k not in norm_abbrev_map:
                            norm_abbrev_map[k] = inf

                # id_to_info remains empty because this endpoint doesn't provide numeric team ids
                standings_cache['data'] = (id_to_info, norm_abbrev_map, id_to_abbrev)
                standings_cache['timestamp'] = datetime.now()
                save_standings_cache()  # Persist to disk
                return id_to_info, norm_abbrev_map, id_to_abbrev
        except Exception:
            # fall through to older approach below
            pass

        # If the 'now' endpoint wasn't available or failed, fall back to the statsapi approach
        teams_url = "https://statsapi.web.nhl.com/api/v1/teams"
        resp = requests.get(teams_url, timeout=10)
        resp.raise_for_status()
        teams_data = resp.json().get('teams', [])

        # Build id -> abbreviation map (try multiple abbrev keys)
        id_to_abbrev = {}
        for t in teams_data:
            tid = t.get('id')
            if not tid:
                continue
            abbrev = t.get('abbreviation') or t.get('triCode') or t.get('abbrev') or t.get('teamAbbrev')
            if isinstance(abbrev, str) and abbrev:
                id_to_abbrev[tid] = abbrev

        # Fetch standings
        standings_url = "https://statsapi.web.nhl.com/api/v1/standings"
        resp = requests.get(standings_url, timeout=10)
        resp.raise_for_status()
        sdata = resp.json()

        for division in sdata.get('records', []):
            for trec in division.get('teamRecords', []):
                team = trec.get('team', {})
                tid = team.get('id')

                # Record
                lr = trec.get('leagueRecord') or trec.get('record') or {}
                try:
                    w = lr.get('wins', 0)
                    l = lr.get('losses', 0)
                    ot = lr.get('ot', lr.get('overtime', lr.get('o', 0)))
                except Exception:
                    w, l, ot = 0, 0, 0

                record_str = f"{w}-{l}-{ot}"

                # Points
                points = trec.get('points')

                # Rank: prefer wildcard/division/conference/league
                rank = None
                for rk in ('wildCardRank', 'divisionRank', 'conferenceRank', 'leagueRank'):
                    rval = trec.get(rk)
                    if rval:
                        rank = rval
                        break

                # Streak
                streak = None
                s = trec.get('streak')
                if isinstance(s, dict):
                    streak = s.get('streakCode') or s.get('streakType') or None
                elif isinstance(s, str):
                    streak = s

                info = {'record': record_str, 'points': points, 'rank': rank, 'streak': streak}

                if tid:
                    id_to_info[tid] = info
                    abbrev = id_to_abbrev.get(tid)
                    if abbrev:
                        abbrev_to_info[abbrev] = info

        # Normalize abbrev keys to support multiple lookup forms (upper/lower/stripped)
        import re
        def _norm_keys(s):
            keys = set()
            if not s:
                return keys
            s0 = str(s).strip()
            keys.add(s0)
            keys.add(s0.upper())
            keys.add(s0.lower())
            # stripped non-alphanumeric
            s_alnum = re.sub(r'[^A-Za-z0-9]', '', s0)
            if s_alnum:
                keys.add(s_alnum)
                keys.add(s_alnum.upper())
            return keys

        norm_abbrev_map = {}
        for a, inf in abbrev_to_info.items():
            for k in _norm_keys(a):
                if k and k not in norm_abbrev_map:
                    norm_abbrev_map[k] = inf

        standings_cache['data'] = (id_to_info, norm_abbrev_map, id_to_abbrev)
        standings_cache['timestamp'] = datetime.now()
        save_standings_cache()  # Persist to disk
        return id_to_info, norm_abbrev_map, id_to_abbrev
    except Exception as e:
        try:
            import traceback
            print("Debug: fetch_standings_records failed:")
            traceback.print_exc()
        except Exception:
            print(f"Debug: fetch_standings_records failed: {e}")
        return {}, {}, {}


def fetch_where_to_watch(game_id):
    """Query multiple forms of the where-to-watch endpoint and return a
    list of human-readable strings describing how/where to view the game.

    Tries several URL shapes to be tolerant of API variants. Returns an
    empty list on error or when no useful info is found.
    """
    if not game_id:
        return []

    base = "https://api-web.nhle.com/v1/where-to-watch"
    candidates = [f"{base}/{game_id}", f"{base}?gamePk={game_id}", f"{base}?gameId={game_id}", f"{base}?id={game_id}"]

    resp_json = None
    for url in candidates:
        try:
            r = requests.get(url, timeout=6)
            if r.status_code != 200:
                continue
            resp_json = r.json()
            if resp_json:
                break
        except Exception:
            continue

    if not resp_json:
        return []

    out_lines = []

    # If the endpoint returns a top-level list (common), handle that shape.
    if isinstance(resp_json, list):
        try:
            # Prioritize entries for US/CA where fans typically look
            prioritized = []
            others = []
            for entry in resp_json:
                country = entry.get('countryName') or entry.get('countryCode') or entry.get('id')
                primary = entry.get('primaryBroadcastName') or entry.get('primaryBroadcast') or entry.get('primary')
                streaming = entry.get('streamingName') or entry.get('streaming')
                streaming_url = entry.get('streamingSiteUrl') or entry.get('streamingUrl') or entry.get('streamingSite')

                pieces = []
                if primary:
                    pieces.append(str(primary))
                if streaming:
                    pieces.append(str(streaming))
                if streaming_url:
                    pieces[-1] = pieces[-1] + f" ({streaming_url})" if pieces else f"{streaming} ({streaming_url})"

                line = f"{country}: {', '.join(pieces)}" if pieces else f"{country}"

                cc = (entry.get('countryCode') or '').upper()
                if cc in ('US', 'CA'):
                    prioritized.append(line)
                else:
                    others.append(line)

            # Return prioritized entries if present, else a short set of entries
            if prioritized:
                out_lines.extend(prioritized)
            else:
                out_lines.extend(others[:6])
            # dedupe, sanitize URLs/domains, and return early since we've handled the list shape
            import re
            seen = set()
            cleaned = []
            for l in out_lines:
                ls = str(l).strip()
                if not ls:
                    continue
                # remove any parenthetical content that looks like a URL or domain
                ls = re.sub(r'\([^)]*(?:https?://|www\.|[^\s()]*\.[^\s()]+)[^)]*\)', '', ls)
                # remove any remaining http/https URLs
                ls = re.sub(r'https?://\S+', '', ls)
                # collapse whitespace and trim
                ls = re.sub(r'\s+', ' ', ls).strip()
                # remove empty parentheses
                ls = re.sub(r"\(\s*\)", '', ls).strip()
                # clean up commas left by removals
                ls = re.sub(r',\s*,+', ',', ls)
                ls = re.sub(r',\s*$', '', ls).strip()
                if not ls or ls in seen:
                    continue
                seen.add(ls)
                cleaned.append(ls)
            return cleaned
        except Exception:
            # fall through to generic parsing when list handling fails
            pass

    # Try several known shapes
    try:
        # Common: top-level 'broadcasts' list
        if isinstance(resp_json.get('broadcasts'), list):
            for b in resp_json.get('broadcasts') or []:
                typ = b.get('type') or b.get('medium') or b.get('broadcastType') or 'TV'
                name = b.get('name') or b.get('network') or b.get('callSign') or b.get('broadcaster')
                market = b.get('market') or b.get('country') or None
                s = f"{typ}: {name}" if name else f"{typ}"
                if market:
                    s = f"{s} ({market})"
                out_lines.append(s)

        # Common: 'markets' -> each market may contain 'coverage' or 'broadcasts'
        if isinstance(resp_json.get('markets'), list):
            for m in resp_json.get('markets') or []:
                mname = m.get('marketName') or m.get('name') or None
                cov = m.get('coverage') or m.get('broadcasts') or m.get('coverageList') or []
                if isinstance(cov, list) and cov:
                    for c in cov:
                        cname = c.get('name') or c.get('network') or c.get('callSign') or None
                        ctyp = c.get('type') or c.get('medium') or 'TV'
                        if cname:
                            if mname:
                                out_lines.append(f"{ctyp}: {cname} ({mname})")
                            else:
                                out_lines.append(f"{ctyp}: {cname}")

        # Some payloads return a simple mapping under 'whereToWatch' or 'where'
        for key in ('whereToWatch', 'where', 'where_to_watch'):
            if key in resp_json:
                v = resp_json.get(key)
                if isinstance(v, str):
                    out_lines.append(v)
                elif isinstance(v, dict):
                    # flatten small dicts
                    for kk, vv in v.items():
                        out_lines.append(f"{kk}: {vv}")

        # Fallback: search the JSON for any network-like names
        if not out_lines:
            def _collect_networks(o, acc):
                if isinstance(o, dict):
                    for kk, vv in o.items():
                        if kk.lower() in ('network', 'name', 'callSign', 'broadcaster', 'station') and isinstance(vv, (str, int)):
                            acc.append(str(vv))
                        else:
                            _collect_networks(vv, acc)
                elif isinstance(o, list):
                    for item in o:
                        _collect_networks(item, acc)

            nets = []
            _collect_networks(resp_json, nets)
            for n in nets:
                out_lines.append(f"Network: {n}")

    except Exception:
        # be conservative on parse errors
        return []

    # Deduplicate, sanitize URLs, and return
    import re
    seen = set()
    cleaned = []
    for l in out_lines:
        ls = str(l).strip()
        if not ls:
            continue
        # remove any parenthetical content that looks like a URL or domain (e.g. '(https://...)' or '(nhllive.com)')
        ls = re.sub(r'\([^)]*(?:https?://|www\.|[^\s()]*\.[^\s()]+)[^)]*\)', '', ls)
        # remove any remaining http/https URLs
        ls = re.sub(r'https?://\S+', '', ls)
        # collapse multiple spaces and strip leftover punctuation/whitespace
        ls = re.sub(r'\s+', ' ', ls).strip()
        # remove empty parentheses left after URL/domain removal
        ls = re.sub(r"\(\s*\)", '', ls).strip()
        # remove trailing commas or duplicate commas introduced by removal
        ls = re.sub(r',\s*,+', ',', ls)
        ls = re.sub(r',\s*$', '', ls).strip()
        if not ls:
            continue
        if ls in seen:
            continue
        seen.add(ls)
        cleaned.append(ls)

    return cleaned


def fetch_skater_stat_leaders(categories=None, limit=-1):
    """Fetch current skater stat leaders for the given comma-separated
    categories (e.g. 'goals,assists,points'). Returns a dict mapping
    category -> list of leader entries {playerId, playerName, value}.

    With caching: if data was fetched within CACHE_DURATION seconds, return cached data.

    This function is defensive: on any error it returns an empty dict.
    """
    # Check cache first
    if leaders_cache.get('data') and leaders_cache.get('timestamp'):
        elapsed = (datetime.now() - leaders_cache['timestamp']).total_seconds()
        if elapsed < CACHE_DURATION:
            sys.stderr.write(f"[CACHE] Using cached leaders (age: {elapsed:.0f}s)\n")
            return leaders_cache['data']
    
    sys.stderr.write(f"[LEADERS] Cache miss, fetching fresh leaders...\n")
    if categories is None:
        categories = ['goals', 'assists', 'points']
    if isinstance(categories, (list, tuple)):
        cat_str = ','.join(categories)
    else:
        cat_str = str(categories)

    url = f"https://api-web.nhle.com/v1/skater-stats-leaders/current?categories={cat_str}&limit={limit}"
    try:
        api_start = time.time()
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        api_time = (time.time() - api_start) * 1000
        sys.stderr.write(f"[API] Leaders endpoint: {api_time:.0f}ms\n")
        data = r.json()
        # Expecting either dict with category keys or a list — handle both
        out = {}
        if isinstance(data, dict):
            # common shape: data['leaders'] or mapping directly
            if 'leaders' in data and isinstance(data['leaders'], dict):
                src = data['leaders']
            else:
                src = data

            for k, v in src.items():
                # v expected to be list of entries
                if isinstance(v, list):
                    lst = []
                    for ent in v:
                        # try to find id/name/value in several places
                        pid = ent.get('playerId') or ent.get('id') or (ent.get('player') or {}).get('id')
                        # try multiple name shapes
                        pname = ent.get('playerName') or (ent.get('player') and ent.get('player').get('fullName') if isinstance(ent.get('player'), dict) else ent.get('playerName'))
                        # assemble from firstName/lastName when needed
                        if not pname:
                            fn = ent.get('firstName') or (ent.get('player') or {}).get('firstName')
                            ln = ent.get('lastName') or (ent.get('player') or {}).get('lastName')
                            def _nval(x):
                                if x is None:
                                    return ''
                                if isinstance(x, dict):
                                    return x.get('default') or x.get('value') or ''
                                return str(x)
                            if fn or ln:
                                pname = f"{_nval(fn)} {_nval(ln)}".strip()
                        # try to capture team info when available (API uses several shapes)
                        team_id = None
                        team_abbrev = None
                        # prefer explicit shorthand field when present
                        if ent.get('teamAbbrev'):
                            team_abbrev = ent.get('teamAbbrev')
                        if isinstance(ent.get('team'), dict):
                            team_id = ent.get('team').get('id')
                            team_abbrev = team_abbrev or ent.get('team').get('triCode') or ent.get('team').get('abbreviation') or ent.get('team').get('abbrev')
                        # sometimes team is nested under player
                        if not team_abbrev and isinstance(ent.get('player'), dict):
                            ct = (ent.get('player') or {}).get('currentTeam') or {}
                            if isinstance(ct, dict):
                                team_id = team_id or ct.get('id')
                                team_abbrev = ct.get('triCode') or ct.get('abbreviation') or ct.get('abbrev')
                        # fallback: try 'player' dict
                        if not pname and isinstance(ent.get('player'), dict):
                            pname = ent.get('player').get('fullName') or (ent.get('player').get('firstName','') + ' ' + ent.get('player').get('lastName','')).strip()
                        val = ent.get('value') or ent.get('stat') or ent.get('count') or ent.get('total')
                        lst.append({'playerId': pid, 'playerName': pname, 'value': val, 'teamId': team_id, 'teamAbbrev': team_abbrev})
                    out[str(k)] = lst
        elif isinstance(data, list):
            # Some endpoints might return a list of entries with category field
            # group them by category
            grouped = {}
            processed = []
            for ent in data:
                cat = ent.get('category') or ent.get('type') or 'unknown'
                pid = ent.get('playerId') or ent.get('id') or (ent.get('player') or {}).get('id')
                pname = ent.get('playerName') or (ent.get('player') or {}).get('fullName')
                # assemble from firstName/lastName when needed
                if not pname:
                    fn = ent.get('firstName') or (ent.get('player') or {}).get('firstName')
                    ln = ent.get('lastName') or (ent.get('player') or {}).get('lastName')
                    def _nval2(x):
                        if x is None:
                            return ''
                        if isinstance(x, dict):
                            return x.get('default') or x.get('value') or ''
                        return str(x)
                    if fn or ln:
                        pname = f"{_nval2(fn)} {_nval2(ln)}".strip()
                val = ent.get('value') or ent.get('stat') or ent.get('total')
                team_id = None
                team_abbrev = None
                # top-level shorthand field often present
                if ent.get('teamAbbrev'):
                    team_abbrev = ent.get('teamAbbrev')
                if isinstance(ent.get('team'), dict):
                    team_id = ent.get('team').get('id')
                    team_abbrev = team_abbrev or ent.get('team').get('triCode') or ent.get('team').get('abbreviation') or ent.get('team').get('abbrev')
                if not team_abbrev and isinstance(ent.get('player'), dict):
                    ct = (ent.get('player') or {}).get('currentTeam') or {}
                    if isinstance(ct, dict):
                        team_id = team_id or ct.get('id')
                        team_abbrev = ct.get('triCode') or ct.get('abbreviation') or ct.get('abbrev')
                entry = {'playerId': pid, 'playerName': pname, 'value': val, 'teamId': team_id, 'teamAbbrev': team_abbrev}
                grouped.setdefault(cat, []).append(entry)
                processed.append(entry)
            # If caller requested a single category but the API returned a flat list
            # without category fields, map the processed list to that single category.
            try:
                # categories may be in outer scope as cat_str; fallback to inspecting url param
                requested_single = None
                if isinstance(categories, (list, tuple)) and len(categories) == 1:
                    requested_single = str(categories[0])
                if requested_single:
                    out = {requested_single: grouped.get(requested_single) or grouped.get('unknown') or processed}
                else:
                    out = grouped
            except Exception:
                out = grouped

        # Cache the result before returning
        leaders_cache['data'] = out
        leaders_cache['timestamp'] = datetime.now()
        save_leaders_cache()  # Persist to disk
        return out
    except Exception:
        return {}

def display_todays_games(date_str=None):
    """Display NHL games for the given date string (YYYY-MM-DD).

    If `date_str` is None, defaults to today.
    Accepts special values: 'yesterday' -> previous calendar date.
    """
def display_todays_games(date_str=None, force_color=False, show_leaders=False):
    # allow forcing color for testing
    global FORCE_COLOR
    if force_color:
        FORCE_COLOR = True

    # Normalize special keywords
    if date_str in ('yesterday', 'y'):
        date_obj = datetime.now() - timedelta(days=1)
        date_str = date_obj.strftime('%Y-%m-%d')
    if date_str in ('today', 't', None):
        date_str = datetime.now().strftime('%Y-%m-%d')

    games = get_nhl_games_for_date(date_str)
    
    if not games:
        # Determine the date used for the API call for better user feedback
        now_utc = datetime.now(timezone.utc)
        utc_offset_hours = 5
        now_et = now_utc - timedelta(hours=utc_offset_hours)
        today_et = now_et.strftime('%Y-%m-%d')
        print(f"No NHL games found for today ({today_et}).")
        return
    
    # Pre-fetch all game data concurrently for speed
    print("Loading game details...")
    game_ids = [game.get('id') for game in games if game.get('id')]
    all_game_data = fetch_game_data_batch(game_ids)
    print("✅ Game data loaded!")

    # Fetch standings once for fallback record/points/rank/streak
    id_to_info, abbrev_to_info, id_to_abbrev = fetch_standings_records()
    # Fetch skater leaders for goals/assists/points so we can highlight leaders
    # Call per-category to handle endpoints that return flat lists without category fields
    leaders = {}
    for _cat in ('goals','assists','points'):
        res = fetch_skater_stat_leaders(categories=[_cat], limit=-1)
        lst = res.get(_cat) or res.get(_cat.capitalize()) or res.get('unknown') or []
        leaders[_cat] = lst
    # Build name and id sets for quick matching (prefer id matching)
    def _norm_name(n):
        if not n:
            return ''
        try:
            return ' '.join(str(n).lower().split())
        except Exception:
            return str(n).lower()

    leader_names = {'goals': set(), 'assists': set(), 'points': set()}
    leader_ids = {'goals': set(), 'assists': set(), 'points': set()}
    # Temporaries to compute league-wide top values per category
    _leader_entries = {'goals': [], 'assists': [], 'points': []}
    # Also build per-team leader lookup: {category: {team_key: player_id_or_name}}
    leader_by_team_ids = {'goals': {}, 'assists': {}, 'points': {}}
    leader_by_team_names = {'goals': {}, 'assists': {}, 'points': {}}
    # track numeric values per team to pick the best leader (higher is better)
    leader_by_team_vals = {'goals': {}, 'assists': {}, 'points': {}}
    for cat in ('goals','assists','points'):
        vals = leaders.get(cat) or leaders.get(cat.capitalize()) or leaders.get(cat.upper()) or []
        import re
        def _norm_abbrev_key(s):
            if not s:
                return ''
            s0 = str(s).strip().upper()
            return re.sub(r'[^A-Z0-9]', '', s0)
        for e in vals:
            if not isinstance(e, dict):
                continue
            pid = e.get('playerId') or e.get('id') or (e.get('player') or {}).get('id')
            pname = e.get('playerName') or (e.get('player') or {}).get('fullName')
            val = e.get('value')
            # try to discover team abbrev for per-team leader mapping
            team_abbrev = None
            try:
                # prefer explicit shorthand fields present in some payloads
                if e.get('teamAbbrev'):
                    team_abbrev = e.get('teamAbbrev')
                te = e.get('team') or {}
                if isinstance(te, dict):
                    team_abbrev = team_abbrev or te.get('triCode') or te.get('abbreviation') or te.get('abbrev') or te.get('teamAbbrev')
                elif isinstance(te, str):
                    team_abbrev = team_abbrev or te
                if not team_abbrev and isinstance(e.get('player'), dict):
                    ct = (e.get('player') or {}).get('currentTeam') or {}
                    if isinstance(ct, dict):
                        team_abbrev = ct.get('triCode') or ct.get('abbreviation') or ct.get('abbrev')
                # fallback: if we have teamId, map via id_to_abbrev
                if not team_abbrev:
                    tid = e.get('teamId') or (e.get('player') or {}).get('currentTeam', {}).get('id')
                    if tid and id_to_abbrev and tid in id_to_abbrev:
                        team_abbrev = id_to_abbrev.get(tid)
            except Exception:
                team_abbrev = None
            if pid:
                try:
                    pid_val = int(pid)
                except Exception:
                    pid_val = str(pid)
                _leader_entries[cat].append((pid_val, _norm_name(pname) if pname else '', val))
            elif pname:
                # record entries without id as name-only entries
                _leader_entries[cat].append((None, _norm_name(pname), val))
            # register per-team leaders (prefer pid then name)
            if team_abbrev:
                k = _norm_abbrev_key(team_abbrev)
                try:
                    vnum = int(val) if isinstance(val, (int, str)) and str(val).isdigit() else (float(val) if isinstance(val, (int, float, str)) and str(val).replace('.', '', 1).isdigit() else None)
                except Exception:
                    vnum = None
                prev = leader_by_team_vals[cat].get(k)
                # choose entry with larger value when possible
                if vnum is not None and (prev is None or vnum > prev):
                    leader_by_team_vals[cat][k] = vnum
                    if pid:
                        try:
                            leader_by_team_ids[cat][k] = int(pid)
                        except Exception:
                            leader_by_team_ids[cat][k] = str(pid)
                        # ensure name map also populated
                        if pname:
                            leader_by_team_names[cat][k] = _norm_name(pname)
                    elif pname:
                        leader_by_team_names[cat][k] = _norm_name(pname)
    # Leaders are computed above. Debug printing of per-team leader maps has been
    # intentionally disabled to keep output concise. Use --no-show-leaders to
    # suppress computation if you want to avoid the overhead.
    # Build league-global leader id/name sets but only keep highest-value leaders
    try:
        for cat in ('goals','assists','points'):
            entries = _leader_entries.get(cat, []) or []
            max_val = None
            parsed = []
            for pid_val, pname_norm, v in entries:
                vnum = None
                try:
                    if v is None:
                        vnum = None
                    elif isinstance(v, (int, float)):
                        vnum = v
                    elif isinstance(v, str) and v.strip().isdigit():
                        vnum = int(v.strip())
                    else:
                        # float-ish
                        vv = str(v).strip()
                        if vv.replace('.', '', 1).isdigit():
                            vnum = float(vv)
                except Exception:
                    vnum = None
                parsed.append((pid_val, pname_norm, vnum))
                if vnum is not None and (max_val is None or vnum > max_val):
                    max_val = vnum
            # include only entries that match max_val
            for pid_val, pname_norm, vnum in parsed:
                if vnum is not None and max_val is not None and vnum == max_val:
                    if pid_val is not None:
                        leader_ids[cat].add(pid_val)
                    if pname_norm:
                        leader_names[cat].add(pname_norm)
    except Exception:
        # fallback: leave leader_ids/names as-is (may be empty)
        pass
    # Helper to produce small symbols for league-leading players
    def _league_leader_symbols(pid, name_norm):
        # symbols: Points=★, Goals=⚽, Assists=🅰️
        syms = []
        try:
            if pid is not None and pid in leader_ids.get('points', set()):
                syms.append('★')
            elif name_norm and name_norm in leader_names.get('points', set()):
                syms.append('★')
            if pid is not None and pid in leader_ids.get('goals', set()):
                syms.append('⚽')
            elif name_norm and name_norm in leader_names.get('goals', set()):
                syms.append('⚽')
            if pid is not None and pid in leader_ids.get('assists', set()):
                syms.append('🅰️')
            elif name_norm and name_norm in leader_names.get('assists', set()):
                syms.append('🅰️')
        except Exception:
            return ''
        return ''.join(syms)
    # Diagnostic info: how many teams/abbrevs loaded
    # diagnostic prints removed
    
    # Use the provided date or date from one of the games
    if games:
        # games from schedule usually include a 'gameDate' key, but use provided date_str
        game_date_str = date_str or games[0].get('gameDate', datetime.now().strftime('%Y-%m-%d'))
        try:
            display_date = datetime.strptime(game_date_str, '%Y-%m-%d').strftime('%A, %B %d, %Y')
        except:
            display_date = datetime.now().strftime('%A, %B %d, %Y') # Fallback
    else:
        # Should not happen if 'games' is not empty; fallback display date
        display_date = datetime.now().strftime('%A, %B %d, %Y')
        
    print("=" * 80)
    # Header: show which day is being displayed (do not assume 'today')
    # Color the date: green if it's today, red if it's in the past
    try:
        req_date_obj = datetime.strptime(game_date_str, '%Y-%m-%d').date()
    except Exception:
        req_date_obj = None
    today_date = datetime.now().date()
    colored_display_date = display_date
    try:
        if req_date_obj is not None:
            if req_date_obj == today_date:
                colored_display_date = _apply_styles(display_date, color_name='green')
            elif req_date_obj < today_date:
                colored_display_date = _apply_styles(display_date, color_name='red')
    except Exception:
        colored_display_date = display_date

    print(f"{'NHL GAMES':^80}")
    print(f"{colored_display_date:^80}")
    print("=" * 80)
    # Legend for highlights
    try:
        # Points leader: underline; Goals leader: yellow; Assists leader: magenta
        legend_points = _apply_styles('Points leader (team)', underline=True)
        legend_goals = _color_text('Goals leader (team)', 'yellow')
        legend_assists = _color_text('Assists leader (team)', 'magenta')
        print(f"Highlights: {legend_points} | {legend_goals} | {legend_assists}")
    except Exception:
        # best-effort: if color funcs fail, print plain
        print("Highlights: Points leader (team) | Goals leader (team) | Assists leader (team)")
    
    completed_games = 0
    live_games = 0
    scheduled_games = 0
    
    for game in games:
        # Resolve team abbrevs from possible keys
        def _abbrev(tobj):
            # Robustly resolve an abbreviation from several possible shapes
            if not tobj:
                return ''
            if isinstance(tobj, str):
                return tobj
            if not isinstance(tobj, dict):
                return ''
            for key in ('abbrev', 'abbreviation', 'triCode', 'teamAbbrev', 'shortName', 'name'):
                val = tobj.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            # Sometimes the real team object is nested under 'team'
            nested = tobj.get('team') if isinstance(tobj.get('team'), dict) else None
            if nested:
                for key in ('abbrev', 'abbreviation', 'triCode', 'teamAbbrev', 'shortName', 'name'):
                    val = nested.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            # Last resort: check keys that might be numeric or id-like
            for key in ('tri_code', 'team_abbrev'):
                val = tobj.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            return ''

        # game_id and detailed payload for this game (used below)
        game_id = game.get('id')
        detailed_game = all_game_data.get(game_id, {}) or {}
        away_team_obj = detailed_game.get('awayTeam') or game.get('awayTeam') or {}
        home_team_obj = detailed_game.get('homeTeam') or game.get('homeTeam') or {}

        # Now resolve display abbreviations (prefer detailed objects)
        away_team = _abbrev(away_team_obj or {})
        home_team = _abbrev(home_team_obj or {})

        away_record = format_team_record(away_team_obj)
        home_record = format_team_record(home_team_obj)

        # Fill from standings maps if missing
        away_id = _get_team_id(away_team_obj) or _get_team_id(game.get('awayTeam', {}))
        home_id = _get_team_id(home_team_obj) or _get_team_id(game.get('homeTeam', {}))
        if not away_record:
            info = None
            if away_id:
                info = id_to_info.get(away_id)
            else:
                info = abbrev_to_info.get(away_team) if away_team else None
            if info:
                away_record = info.get('record') if isinstance(info, dict) else info
        if not home_record:
            info = None
            if home_id:
                info = id_to_info.get(home_id)
            else:
                info = abbrev_to_info.get(home_team) if home_team else None
            if info:
                home_record = info.get('record') if isinstance(info, dict) else info

        # Scores: schedule payload or detailed boxscore
        away_score = (game.get('awayTeam') or {}).get('score')
        home_score = (game.get('homeTeam') or {}).get('score')
        if away_score is None or home_score is None:
            # try detailed payload shapes
            try:
                bs = detailed_game.get('boxscore') or detailed_game
                away_score = away_score if away_score is not None else bs.get('awayTeam', {}).get('score') or bs.get('linescore', {}).get('away', {}).get('goals')
                home_score = home_score if home_score is not None else bs.get('homeTeam', {}).get('score') or bs.get('linescore', {}).get('home', {}).get('goals')
            except Exception:
                away_score = away_score or 0
                home_score = home_score or 0

        # Determine game state robustly
        raw_state = game.get('gameState') or (game.get('status') or {}).get('type', {}).get('state') or (game.get('status') or {}).get('detailedState') or (game.get('status') or {}).get('abstractGameState')
        state = (str(raw_state) if raw_state is not None else '').upper()

        # Build header and print
        def _build_label(abbrev, record, info):
            # Always return an enriched label: abbrev (W-L-OT, Streak, #Rank, N pts)
            # Fill with placeholders when data is missing.
            record_val = ''
            streak_val = '—'
            rank_val = '#—'
            pts_val = '— pts'

            if record:
                record_val = str(record)
            elif isinstance(info, dict) and info.get('record'):
                record_val = str(info.get('record'))
            else:
                record_val = '—'

            if isinstance(info, dict):
                if info.get('streak'):
                    streak_val = str(info.get('streak'))
                if info.get('rank'):
                    rank_val = f"#{info.get('rank')}"
                if info.get('points') is not None:
                    pts_val = f"{info.get('points')} pts"

            return f"{abbrev} ({record_val}, {streak_val}, {rank_val}, {pts_val})"

        # Prefer id lookup, but if id exists and isn't in id_to_info, fall back to abbrev map
        away_info = None
        if away_id:
            away_info = id_to_info.get(away_id)
        if not away_info and away_team:
            away_info = abbrev_to_info.get(away_team)

        home_info = None
        if home_id:
            home_info = id_to_info.get(home_id)
        if not home_info and home_team:
            home_info = abbrev_to_info.get(home_team)

        # DEBUG: print resolved info for the first game to verify mapping
        # end per-game diagnostics

        # Diagnostic: collect unmatched teams for later reporting
        if not away_info:
            try:
                _unmatched = display_todays_games.__dict__.setdefault('_unmatched', [])
                _unmatched.append(('away', away_id, away_team, away_team_obj.get('name') if isinstance(away_team_obj, dict) else ''))
            except Exception:
                pass
        if not home_info:
            try:
                _unmatched = display_todays_games.__dict__.setdefault('_unmatched', [])
                _unmatched.append(('home', home_id, home_team, home_team_obj.get('name') if isinstance(home_team_obj, dict) else ''))
            except Exception:
                pass

        matchup_label = f"{_build_label(away_team, away_record, away_info)} @ {_build_label(home_team, home_record, home_info)}"
        filler_len = max(1, 60 - len(matchup_label))
        print(f"\n┌─── {matchup_label} {'─' * filler_len}┐")

        # Completed
        if state in ('FINAL', 'FINAL/OT', 'FINAL/SO', 'OFF', 'COMPLETED', 'F'):
            completed_games += 1
            # color winner green when tty
            use_color = sys.stdout.isatty() if hasattr(sys.stdout, 'isatty') else False
            def col(t,c):
                return _color_text(t,c) if use_color else t

            if (away_score or 0) > (home_score or 0):
                left = col(f"{away_team} {away_score}",'green')
                right = f"{home_score} {home_team}"
            elif (home_score or 0) > (away_score or 0):
                left = f"{away_team} {away_score}"
                right = col(f"{home_score} {home_team}",'green')
            else:
                left = f"{away_team} {away_score}"
                right = f"{home_score} {home_team}"

            print(f"│ ✅ FINAL: {left} - {right}")

            game_data = all_game_data.get(game_id)
            scoring = format_scoring_summary(game_id, game_data)
            if scoring:
                print("│")
                print("│ ⚽ SCORING SUMMARY:")
                periods = {}
                for goal in scoring:
                    periods.setdefault(goal['period'], []).append(goal)
                def get_period_sort_key(period_name):
                    if period_name.startswith('Period '):
                        try:
                            return int(period_name.replace('Period ', ''))
                        except Exception:
                            return 99
                    if period_name == 'OT':
                        return 4
                    if period_name.startswith('OT'):
                        ot_num = period_name.replace('OT', '')
                        return 3 + int(ot_num) if ot_num.isdigit() else 4
                    if period_name == 'SO':
                        return 10
                    return 99

                period_order = sorted(periods.keys(), key=get_period_sort_key)
                for period in period_order:
                    print(f"│  {period}:")
                    for goal in periods[period]:
                        time_str = f" - {goal['time']}" if goal.get('time') else ""
                        assists_list = goal.get('assists') or []
                        # Normalize assists into compact display strings
                        try:
                            # flatten one level if elements are lists
                            flat = []
                            for item in assists_list:
                                if isinstance(item, list):
                                    flat.extend(item)
                                else:
                                    flat.append(item)
                            assists_list = flat
                            norm_assists = [_normalize_assist_item(a) for a in assists_list]
                            assists_items = [na.get('display') or '' for na in norm_assists]
                        except Exception:
                            assists_items = [str(a) for a in assists_list]

                        # If any item looks like a stringified list/dict, try to parse and normalize further
                        final_items = []
                        import ast
                        for s in assists_items:
                            if not s:
                                continue
                            ss = str(s).strip()
                            if 'playerId' in ss and '{' in ss:
                                try:
                                    # extract substring starting at first '{' to account for prefixed text
                                    start = ss.find('{')
                                    sub = ss[start:]
                                    to_parse = sub
                                    if sub.startswith('{') and ("}, {" in sub or "},\n {" in sub or '}{' in sub):
                                        to_parse = f"[{sub}]"
                                    parsed = ast.literal_eval(to_parse)
                                    if isinstance(parsed, list):
                                        for inner in parsed:
                                            na = _normalize_assist_item(inner)
                                            if na.get('display'):
                                                final_items.append(na.get('display'))
                                    elif isinstance(parsed, dict):
                                        na = _normalize_assist_item(parsed)
                                        if na.get('display'):
                                            final_items.append(na.get('display'))
                                    continue
                                except Exception:
                                    pass
                            final_items.append(ss)

                        assists_str = f" (Assists: {', '.join(final_items)})" if final_items else ""

                        # Highlight scorer/assists when they are leaders
                        # scorer is structured: {'id','name','display'}
                        s = goal.get('scorer') or {}
                        scorer_display = s.get('display') if isinstance(s, dict) else str(s)
                        scorer_id = s.get('id') if isinstance(s, dict) else None
                        scorer_name_norm = _norm_name(s.get('name') if isinstance(s, dict) else scorer_display)

                        # Team-aware highlighting: prefer team leaders (points > goals)
                        try:
                            # normalize team key from goal team abbrev
                            team_key = ''
                            try:
                                team_key = _norm_abbrev_key(goal.get('team') or '')
                            except Exception:
                                team_key = ''

                            # DEBUG: inspect specific scorer mapping (temporary)
                            try:
                                pass
                            except Exception:
                                pass

                            highlighted = False
                            # Team-aware highlighting: goals leader -> yellow; points leader -> underline
                            try:
                                team_points_leader_id = leader_by_team_ids.get('points', {}).get(team_key)
                                team_points_leader_name = leader_by_team_names.get('points', {}).get(team_key)
                                team_goals_leader_id = leader_by_team_ids.get('goals', {}).get(team_key)
                                team_goals_leader_name = leader_by_team_names.get('goals', {}).get(team_key)

                                # Goals leader: color yellow, underline if also points leader
                                if team_goals_leader_id and scorer_id is not None and team_goals_leader_id == (int(scorer_id) if isinstance(scorer_id, (int, str)) and str(scorer_id).isdigit() else scorer_id):
                                    is_points_leader = False
                                    if team_points_leader_id and scorer_id is not None and team_points_leader_id == (int(scorer_id) if isinstance(scorer_id, (int, str)) and str(scorer_id).isdigit() else scorer_id):
                                        is_points_leader = True
                                    elif team_points_leader_name and scorer_name_norm and scorer_name_norm == team_points_leader_name:
                                        is_points_leader = True
                                    scorer_display = _apply_styles(scorer_display, color_name='yellow', underline=is_points_leader)
                                    highlighted = True
                                elif team_goals_leader_name and scorer_name_norm and scorer_name_norm == team_goals_leader_name:
                                    is_points_leader = False
                                    if team_points_leader_name and scorer_name_norm and scorer_name_norm == team_points_leader_name:
                                        is_points_leader = True
                                    scorer_display = _apply_styles(scorer_display, color_name='yellow', underline=is_points_leader)
                                    highlighted = True

                                # Points leader only (not goals leader): underline
                                if not highlighted:
                                    if team_points_leader_id and scorer_id is not None and team_points_leader_id == (int(scorer_id) if isinstance(scorer_id, (int, str)) and str(scorer_id).isdigit() else scorer_id):
                                        scorer_display = _apply_styles(scorer_display, underline=True)
                                        highlighted = True
                                    elif team_points_leader_name and scorer_name_norm and scorer_name_norm == team_points_leader_name:
                                        scorer_display = _apply_styles(scorer_display, underline=True)
                                        highlighted = True
                            except Exception:
                                pass

                            # Fallback to league-global leaders if no team leader determined
                            if not highlighted:
                                try:
                                    if scorer_id is not None and scorer_id in leader_ids.get('points', set()):
                                        scorer_display = _apply_styles(scorer_display, underline=True)
                                    elif scorer_id is not None and scorer_id in leader_ids.get('goals', set()):
                                        scorer_display = _apply_styles(scorer_display, color_name='yellow')
                                    else:
                                        if scorer_name_norm in leader_names.get('points', set()):
                                            scorer_display = _apply_styles(scorer_display, underline=True)
                                        elif scorer_name_norm in leader_names.get('goals', set()):
                                            scorer_display = _apply_styles(scorer_display, color_name='yellow')
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        # Append league-global leader symbols (if any) to scorer display
                        try:
                            sym = _league_leader_symbols(scorer_id, scorer_name_norm)
                            if sym:
                                scorer_display = f"{scorer_display} {sym}"
                        except Exception:
                            pass

                        # Assist highlighting: assists_list contains dicts with 'display' and 'id'
                        try:
                            highlighted_assists = []
                            # ensure we operate on normalized assist dicts
                            # flatten one level if elements are lists
                            flat = []
                            for item in assists_list:
                                if isinstance(item, list):
                                    flat.extend(item)
                                else:
                                    flat.append(item)
                            assists_list = flat
                            norm_assists = [_normalize_assist_item(a) for a in assists_list]
                            for na in norm_assists:
                                aid = na.get('id')
                                adisplay = na.get('display') or na.get('name') or ''
                                anorm = _norm_name(na.get('name') or adisplay)
                                try:
                                    # team-aware: find team key
                                    team_key = ''
                                    try:
                                        team_key = _norm_abbrev_key(goal.get('team') or '')
                                    except Exception:
                                        team_key = ''

                                    # Team points leader: underline; assists leader: magenta
                                    team_points_leader = leader_by_team_ids.get('points', {}).get(team_key) or leader_by_team_names.get('points', {}).get(team_key)
                                    team_assist_leader = leader_by_team_ids.get('assists', {}).get(team_key) or leader_by_team_names.get('assists', {}).get(team_key)

                                    # If this assist provider is the team's assists leader, color magenta and underline if also points leader
                                    is_points = False
                                    if team_points_leader and aid is not None and team_points_leader == (int(aid) if isinstance(aid, (int, str)) and str(aid).isdigit() else aid):
                                        is_points = True
                                    if team_points_leader and anorm and anorm == team_points_leader:
                                        is_points = True

                                    if team_assist_leader and aid is not None and team_assist_leader == (int(aid) if isinstance(aid, (int, str)) and str(aid).isdigit() else aid):
                                        item = _apply_styles(adisplay, color_name='magenta', underline=is_points)
                                        try:
                                            sym = _league_leader_symbols(aid, anorm)
                                            if sym:
                                                item = f"{item} {sym}"
                                        except Exception:
                                            pass
                                        highlighted_assists.append(item)
                                        continue
                                    if team_assist_leader and anorm and anorm == team_assist_leader:
                                        item = _apply_styles(adisplay, color_name='magenta', underline=is_points)
                                        try:
                                            sym = _league_leader_symbols(aid, anorm)
                                            if sym:
                                                item = f"{item} {sym}"
                                        except Exception:
                                            pass
                                        highlighted_assists.append(item)
                                        continue

                                    # If not assists leader but is points leader -> underline
                                    if is_points:
                                        item = _apply_styles(adisplay, underline=True)
                                        try:
                                            sym = _league_leader_symbols(aid, anorm)
                                            if sym:
                                                item = f"{item} {sym}"
                                        except Exception:
                                            pass
                                        highlighted_assists.append(item)
                                        continue

                                    # Fallback to league-global assists leader
                                    if aid is not None and (int(aid) if isinstance(aid, (int, str)) and str(aid).isdigit() else aid) in leader_ids.get('assists', set()):
                                        item = _apply_styles(adisplay, color_name='magenta')
                                        try:
                                            sym = _league_leader_symbols(aid, anorm)
                                            if sym:
                                                item = f"{item} {sym}"
                                        except Exception:
                                            pass
                                        highlighted_assists.append(item)
                                    elif anorm in leader_names.get('assists', set()):
                                        item = _apply_styles(adisplay, color_name='magenta')
                                        try:
                                            sym = _league_leader_symbols(aid, anorm)
                                            if sym:
                                                item = f"{item} {sym}"
                                        except Exception:
                                            pass
                                        highlighted_assists.append(item)
                                    else:
                                        item = adisplay
                                        try:
                                            sym = _league_leader_symbols(aid, anorm)
                                            if sym:
                                                item = f"{item} {sym}"
                                        except Exception:
                                            pass
                                        highlighted_assists.append(item)
                                except Exception:
                                    if anorm in leader_names.get('assists', set()):
                                        highlighted_assists.append(_color_text(adisplay, 'magenta'))
                                    else:
                                        highlighted_assists.append(adisplay)
                            if highlighted_assists:
                                assists_str = f" (Assists: {', '.join(highlighted_assists)})"
                        except Exception:
                            pass

                        # Append league-global leader symbols (if any) to scorer display
                        try:
                            sym = _league_leader_symbols(scorer_id, scorer_name_norm)
                            if sym:
                                scorer_display = f"{scorer_display} {sym}"
                        except Exception:
                            pass
                        print(f"│    {goal['team']}: {scorer_display}{time_str}{assists_str}")

            stats = _extract_shots_and_goalies(game_data)
            away_shots, home_shots = stats.get('shots', (None, None))
            away_goalie, home_goalie = stats.get('goalies', (None, None))
            if away_shots is not None or home_shots is not None:
                a_sh = away_shots if away_shots is not None else '-'
                h_sh = home_shots if home_shots is not None else '-'
                print(f"│ Shots: {away_team} {a_sh} - {h_sh} {home_team}")
            if away_goalie or home_goalie:
                ag = away_goalie or '-'
                hg = home_goalie or '-'
                print(f"│ Goalies: {away_team}: {ag} | {home_team}: {hg}")

        elif state in ('LIVE', 'IN_PROGRESS', 'CRIT', 'IN PROGRESS'):
            live_games += 1
            # print live score
            print(f"│  LIVE: {away_team} {away_score or 0} - {home_score or 0} {home_team}")
            period_info = (game.get('periodDescriptor') or {}).get('number') or (game.get('currentPeriod') or '')
            time_remaining = (game.get('clock') or {}).get('timeRemaining')
            if period_info and time_remaining:
                print(f"│ ⏰ Period {period_info} - {time_remaining}")
            else:
                print("│ ⏰ Game in progress")

            game_data = all_game_data.get(game_id)
            scoring = format_scoring_summary(game_id, game_data)
            if scoring:
                print("│")
                print("│ ⚽ SCORING SO FAR:")
                periods = {}
                for goal in scoring:
                    periods.setdefault(goal['period'], []).append(goal)
                period_order = sorted(periods.keys(), key=get_period_sort_key)
                for period in period_order:
                    print(f"│  {period}:")
                    for goal in periods[period]:
                        time_str = f" - {goal.get('time')}" if goal.get('time') else ""
                        assists_list = goal.get('assists') or []
                        try:
                            norm_assists = [_normalize_assist_item(a) for a in assists_list]
                            assists_items = [na.get('display') or '' for na in norm_assists]
                        except Exception:
                            assists_items = [str(a) for a in assists_list]

                        # Try to parse any stringified list/dict assist items and normalize them
                        final_items = []
                        import ast
                        for s in assists_items:
                            if not s:
                                continue
                            ss = str(s).strip()
                            if (ss.startswith('[') or ss.startswith('{')) and 'playerId' in ss:
                                try:
                                    to_parse = ss
                                    # if looks like multiple dicts, wrap as list
                                    if ss.startswith('{') and ("}, {" in ss or "},\n {" in ss or '}{' in ss):
                                        to_parse = f"[{ss}]"
                                    parsed = ast.literal_eval(to_parse)
                                    if isinstance(parsed, list):
                                        for inner in parsed:
                                            na = _normalize_assist_item(inner)
                                            if na.get('display'):
                                                final_items.append(na.get('display'))
                                    elif isinstance(parsed, dict):
                                        na = _normalize_assist_item(parsed)
                                        if na.get('display'):
                                            final_items.append(na.get('display'))
                                    continue
                                except Exception:
                                    pass
                            final_items.append(ss)

                        assists_str = f" (Assists: {', '.join(final_items)})" if final_items else ""

                        # scorer is structured
                        s = goal.get('scorer') or {}
                        scorer_display = s.get('display') if isinstance(s, dict) else str(s)
                        scorer_id = s.get('id') if isinstance(s, dict) else None
                        scorer_name_norm = _norm_name(s.get('name') if isinstance(s, dict) else scorer_display)

                        try:
                            # Team-aware highlighting for live display: goals leader -> yellow; points leader -> underline
                            team_key = ''
                            try:
                                team_key = _norm_abbrev_key(goal.get('team') or '')
                            except Exception:
                                team_key = ''

                            team_points_leader_id = leader_by_team_ids.get('points', {}).get(team_key)
                            team_points_leader_name = leader_by_team_names.get('points', {}).get(team_key)
                            team_goals_leader_id = leader_by_team_ids.get('goals', {}).get(team_key)
                            team_goals_leader_name = leader_by_team_names.get('goals', {}).get(team_key)

                            highlighted_local = False
                            # Goals leader: color yellow, underline if also points leader
                            if team_goals_leader_id and scorer_id is not None and team_goals_leader_id == (int(scorer_id) if isinstance(scorer_id, (int, str)) and str(scorer_id).isdigit() else scorer_id):
                                is_points = False
                                if team_points_leader_id and scorer_id is not None and team_points_leader_id == (int(scorer_id) if isinstance(scorer_id, (int, str)) and str(scorer_id).isdigit() else scorer_id):
                                    is_points = True
                                elif team_points_leader_name and scorer_name_norm and scorer_name_norm == team_points_leader_name:
                                    is_points = True
                                scorer_display = _apply_styles(scorer_display, color_name='yellow', underline=is_points)
                                highlighted_local = True
                            elif team_goals_leader_name and scorer_name_norm and scorer_name_norm == team_goals_leader_name:
                                is_points = False
                                if team_points_leader_name and scorer_name_norm and scorer_name_norm == team_points_leader_name:
                                    is_points = True
                                scorer_display = _apply_styles(scorer_display, color_name='yellow', underline=is_points)
                                highlighted_local = True

                            # Points leader only
                            if not highlighted_local:
                                if team_points_leader_id and scorer_id is not None and team_points_leader_id == (int(scorer_id) if isinstance(scorer_id, (int, str)) and str(scorer_id).isdigit() else scorer_id):
                                    scorer_display = _apply_styles(scorer_display, underline=True)
                                    highlighted_local = True
                                elif team_points_leader_name and scorer_name_norm and scorer_name_norm == team_points_leader_name:
                                    scorer_display = _apply_styles(scorer_display, underline=True)
                                    highlighted_local = True

                            # Fallback to league-global
                            if not highlighted_local:
                                if scorer_id is not None and scorer_id in leader_ids.get('points', set()):
                                    scorer_display = _apply_styles(scorer_display, underline=True)
                                elif scorer_id is not None and scorer_id in leader_ids.get('goals', set()):
                                    scorer_display = _apply_styles(scorer_display, color_name='yellow')
                                else:
                                    if scorer_name_norm in leader_names.get('points', set()):
                                        scorer_display = _apply_styles(scorer_display, underline=True)
                                    elif scorer_name_norm in leader_names.get('goals', set()):
                                        scorer_display = _apply_styles(scorer_display, color_name='yellow')
                        except Exception:
                            pass

                        # highlight assists
                        try:
                            highlighted = []
                            norm_assists = [_normalize_assist_item(a) for a in assists_list]
                            for na in norm_assists:
                                aid = na.get('id')
                                adisplay = na.get('display') or na.get('name') or ''
                                anorm = _norm_name(na.get('name') or adisplay)
                                try:
                                    aid_val = int(aid) if isinstance(aid, (int, str)) and str(aid).isdigit() else aid
                                except Exception:
                                    aid_val = aid

                                # team-aware assist highlighting: assists leader -> magenta; points leader -> underline
                                team_key = ''
                                try:
                                    team_key = _norm_abbrev_key(goal.get('team') or '')
                                except Exception:
                                    team_key = ''
                                team_points_leader = leader_by_team_ids.get('points', {}).get(team_key) or leader_by_team_names.get('points', {}).get(team_key)
                                team_assist_leader = leader_by_team_ids.get('assists', {}).get(team_key) or leader_by_team_names.get('assists', {}).get(team_key)

                                is_points = False
                                if team_points_leader and aid is not None and team_points_leader == (int(aid) if isinstance(aid, (int, str)) and str(aid).isdigit() else aid):
                                    is_points = True
                                if team_points_leader and anorm and anorm == team_points_leader:
                                    is_points = True

                                if team_assist_leader and aid is not None and team_assist_leader == (int(aid) if isinstance(aid, (int, str)) and str(aid).isdigit() else aid):
                                    item = _apply_styles(adisplay, color_name='magenta', underline=is_points)
                                elif team_assist_leader and anorm and anorm == team_assist_leader:
                                    item = _apply_styles(adisplay, color_name='magenta', underline=is_points)
                                elif is_points:
                                    item = _apply_styles(adisplay, underline=True)
                                elif aid_val is not None and (aid_val in leader_ids.get('assists', set())):
                                    item = _apply_styles(adisplay, color_name='magenta')
                                elif anorm in leader_names.get('assists', set()):
                                    item = _apply_styles(adisplay, color_name='magenta')
                                else:
                                    item = adisplay
                                try:
                                    sym = _league_leader_symbols(aid, anorm)
                                    if sym:
                                        item = f"{item} {sym}"
                                except Exception:
                                    pass
                                highlighted.append(item)
                            if highlighted:
                                assists_str = f" (Assists: {', '.join(highlighted)})"
                        except Exception:
                            pass

                        print(f"│    {goal['team']}: {scorer_display}{time_str}{assists_str}")

            stats = _extract_shots_and_goalies(game_data)
            away_shots, home_shots = stats.get('shots', (None, None))
            away_goalie, home_goalie = stats.get('goalies', (None, None))
            if away_shots is not None or home_shots is not None:
                a_sh = away_shots if away_shots is not None else '-'
                h_sh = home_shots if home_shots is not None else '-'
                print(f"│ Shots: {away_team} {a_sh} - {h_sh} {home_team}")
            if away_goalie or home_goalie:
                ag = away_goalie or '-'
                hg = home_goalie or '-'
                print(f"│ Goalies: {away_team}: {ag} | {home_team}: {hg}")

        else:
            scheduled_games += 1
            start_time = game.get('startTimeUTC') or game.get('gameDate')
            if start_time:
                try:
                    game_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    local_time = game_time - timedelta(hours=5)
                    print(f"│  Scheduled: {local_time.strftime('%I:%M %p')} EST")
                except Exception:
                    print(f"│  Status: {state}")
            else:
                print(f"│  Status: {state}")

        # Display 'where to watch' information for the game (always show header)
        try:
            where_lines = fetch_where_to_watch(game_id)
        except Exception:
            where_lines = []

        print("│")
        print("│ 📺 WHERE TO WATCH:")
        if where_lines:
            for wl in where_lines:
                print(f"│   - {wl}")
        else:
            print("│   - (no where-to-watch info found)")

        print(f"└{'─' * 78}┘")
    
    # Summary
    print("\n" + "=" * 80)
    # Summary header indicating the date requested
    summary_header = f"SUMMARY FOR {display_date}"
    # show the colored date in the summary header as well
    try:
        colored_summary_header = f"SUMMARY FOR {colored_display_date}"
    except Exception:
        colored_summary_header = summary_header
    summary_line = f"{completed_games} Completed | {live_games} Live | {scheduled_games} Scheduled"
    print(f"{colored_summary_header:^80}")
    print(f"{summary_line:^80}")
    print("=" * 80)

def main():
    """Main function"""
    try:
        print(" NHL Score Retriever - Today Only")
        parser = argparse.ArgumentParser(description='NHL Score Retriever')
        parser.add_argument('-d', '--date', help="Date to show games for (YYYY-MM-DD). Special: 'yesterday'", default=None)
        # Enable color and leader display by default; provide --no-* counterparts to disable
        parser.add_argument('--force-color', dest='force_color', help='Force ANSI color output even when not a TTY (default: enabled)', action='store_true', default=True)
        parser.add_argument('--no-force-color', dest='force_color', help='Disable ANSI color output', action='store_false')
        parser.add_argument('--show-leaders', dest='show_leaders', help='Print computed per-team leader mappings (default: enabled)', action='store_true', default=True)
        parser.add_argument('--no-show-leaders', dest='show_leaders', help='Do not print computed per-team leader mappings', action='store_false')
        args = parser.parse_args()
        print(f"Fetching games for: {args.date or 'today'}...\n")
        display_todays_games(args.date, force_color=args.force_color, show_leaders=args.show_leaders)
        print("\n✅ Done! Run again to refresh scores.")
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Program interrupted by user.")
    except Exception as e:
        print(f"❌ An error occurred: {e}")
        try:
            import traceback
            traceback.print_exc()
        except Exception:
            pass

# NEW FUNCTION: This is what Django will call.
def get_score_output():
    """
    Runs the main logic and captures all printed output into a string.
    """
    # Create a string buffer
    old_stdout = sys.stdout
    redirected_output = io.StringIO()
    
    # Redirect stdout to the buffer
    sys.stdout = redirected_output
    
    try:
        # Check dependencies before running main logic
        try:
            import requests
        except ImportError:
            # Print error to the redirected output
            print("❌ Error: 'requests' module not found. (Check PythonAnywhere virtual environment)")
            # Return the error message to Django
            sys.stdout = old_stdout # Restore stdout
            return redirected_output.getvalue()
            
        # Run the existing main logic
        main()
        
    except Exception as e:
        # Print exception details to the redirected output
        print(f"\n❌ A critical error occurred in the script execution: {e}")
        
    finally:
        # RESTORE STDOUT! This is crucial.
        sys.stdout = old_stdout
        
    # Return the collected string
    return redirected_output.getvalue()


def get_games_data_skeleton(date_str=None):
    """
    Returns FAST skeleton game data with just schedule info (no slow API calls).
    Used for initial page load - details loaded via AJAX after.
    """
    func_start = time.time()
    sys.stderr.write(f"\n[FUNCTION] get_games_data_skeleton() - FAST MODE\n")
    
    # Normalize date
    if date_str in ('yesterday', 'y'):
        date_obj = datetime.now() - timedelta(days=1)
        date_str = date_obj.strftime('%Y-%m-%d')
    if date_str in ('today', 't', None):
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    games = get_nhl_games_for_date(date_str)
    
    if not games:
        return {
            'success': False,
            'message': f'No NHL games found for {date_str}',
            'games': [],
            'date': date_str
        }
    
    # Build skeleton game data from schedule only (no additional API calls)
    processed_games = []
    for game in games:
        game_id = game.get('id')
        away_team = game.get('awayTeam', {})
        home_team = game.get('homeTeam', {})
        
        away_abbrev = away_team.get('abbrev', '')
        home_abbrev = home_team.get('abbrev', '')
        away_score = away_team.get('score', 0)
        home_score = home_team.get('score', 0)
        
        game_state = game.get('gameState', 'UNKNOWN')
        game_status = 'Scheduled'
        if game_state in ('LIVE', 'CRIT'):
            game_status = 'Live'
        elif game_state in ('FINAL', 'OFF'):
            game_status = 'Final'
        
        # Get start time for scheduled games
        start_time = ''
        start_time_utc = ''
        if game_status == 'Scheduled':
            start_time_utc = game.get('startTimeUTC', '')
            if start_time_utc:
                try:
                    dt_utc = datetime.fromisoformat(start_time_utc.replace('Z', '+00:00'))
                    dt_est = dt_utc - timedelta(hours=5)
                    start_time = dt_est.strftime('%I:%M %p')
                except:
                    start_time = start_time_utc
        
        # Match the structure expected by the template
        processed_games.append({
            'id': game_id,
            'status': game_status,
            'start_time': start_time,
            'start_time_utc': start_time_utc,
            'period_info': '',
            'away_team': {
                'abbrev': away_abbrev,
                'score': away_score,
                'record': '',
                'shots': 0,
                'logo': get_team_logo_path(away_abbrev)
            },
            'home_team': {
                'abbrev': home_abbrev,
                'score': home_score,
                'record': '',
                'shots': 0,
                'logo': get_team_logo_path(home_abbrev)
            },
            'scoring_summary': None,
            'skeleton': True  # Flag to indicate this is skeleton data
        })
    
    func_total = (time.time() - func_start) * 1000
    sys.stderr.write(f"[FUNCTION] Skeleton data ready in {func_total:.0f}ms\n")
    
    # Format display date consistently with full data
    try:
        display_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%A, %B %d, %Y')
    except:
        display_date = date_str
    
    return {
        'success': True,
        'games': processed_games,
        'date': date_str,
        'display_date': display_date,
        'count': len(processed_games),
        'leaders': {'goals': [], 'assists': [], 'points': []},
        'skeleton': True
    }


def get_games_data(date_str=None):
    """
    Returns structured game data as a dictionary for use in Django templates.
    This function provides JSON-serializable data without ANSI color codes.
    """
    func_start = time.time()
    sys.stderr.write(f"\n[FUNCTION] get_games_data() started for date: {date_str}\n")
    
    # Normalize date
    if date_str in ('yesterday', 'y'):
        date_obj = datetime.now() - timedelta(days=1)
        date_str = date_obj.strftime('%Y-%m-%d')
    if date_str in ('today', 't', None):
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    sys.stderr.write(f"[FUNCTION] Normalized date: {date_str}\n")
    
    # Trigger background prefetch of adjacent dates for smooth carousel navigation
    trigger_prefetch_for_date(date_str)
    
    games = get_nhl_games_for_date(date_str)
    
    if not games:
        sys.stderr.write(f"[FUNCTION] No games found for {date_str}\n")
        return {
            'success': False,
            'message': f'No NHL games found for {date_str}',
            'games': [],
            'date': date_str
        }
    
    # Fetch all game data concurrently
    game_ids = [game.get('id') for game in games if game.get('id')]
    sys.stderr.write(f"[FUNCTION] Fetching detailed data for {len(game_ids)} games...\n")
    all_game_data = fetch_game_data_batch(game_ids)
    
    # Fetch standings for team records
    sys.stderr.write(f"[FUNCTION] Fetching team standings...\n")
    standings_start = time.time()
    id_to_info, abbrev_to_info, id_to_abbrev = fetch_standings_records()
    standings_time = (time.time() - standings_start) * 1000
    sys.stderr.write(f"[FUNCTION] Standings fetched in {standings_time:.0f}ms\n")
    
    # Fetch league leaders ONCE for all games (optimization)
    # We'll use this data for both team leader indicators and the leaderboard
    sys.stderr.write(f"[FUNCTION] Fetching league leaders...\n")
    leaders_start = time.time()
    try:
        leaders_data = fetch_skater_stat_leaders(categories=['goals', 'assists', 'points'], limit=100)
        leaders_time = (time.time() - leaders_start) * 1000
        sys.stderr.write(f"[FUNCTION] League leaders fetched in {leaders_time:.0f}ms\n")
        
        # Build team leader maps: {team_abbrev: {category: [player_ids]}}
        team_top_leaders = {}
        for category in ['goals', 'assists', 'points']:
            team_values = {}  # {team: {player_id: value}}
            
            for entry in leaders_data.get(category, []):
                team_abbrev = entry.get('teamAbbrev', '')
                player_id = entry.get('playerId')
                value = entry.get('value', 0)
                
                if team_abbrev and player_id:
                    if team_abbrev not in team_values:
                        team_values[team_abbrev] = {}
                    team_values[team_abbrev][player_id] = value
            
            # Find the leader(s) for each team (highest value)
            for team, players in team_values.items():
                if team not in team_top_leaders:
                    team_top_leaders[team] = {}
                if players:
                    max_value = max(players.values())
                    team_top_leaders[team][category] = [pid for pid, val in players.items() if val == max_value]
    except Exception:
        team_top_leaders = {}
        leaders_data = {'goals': [], 'assists': [], 'points': []}
    
    # Process each game
    processed_games = []
    for game in games:
        game_id = game.get('id')
        game_data = all_game_data.get(game_id) if game_id else None
        
        # Extract team info
        away_team = game.get('awayTeam', {})
        home_team = game.get('homeTeam', {})
        
        away_abbrev = away_team.get('abbrev', '')
        home_abbrev = home_team.get('abbrev', '')
        
        # Get scores
        away_score = away_team.get('score', 0)
        home_score = home_team.get('score', 0)
        
        # Get game state
        game_state = game.get('gameState', 'UNKNOWN')
        game_status = 'Scheduled'
        if game_state in ('LIVE', 'CRIT'):
            game_status = 'Live'
        elif game_state in ('FINAL', 'OFF'):
            game_status = 'Final'
        
        # Get period info
        period_info = ''
        if game_data:
            period = game_data.get('period', game_data.get('currentPeriod', 0))
            clock = game_data.get('clock', game_data.get('periodTimeRemaining', ''))
            if period and game_status == 'Live':
                if period <= 3:
                    period_info = f"Period {period}"
                elif period == 4:
                    period_info = "OT"
                else:
                    period_info = f"OT{period - 3}"
                if clock:
                    period_info += f" - {clock}"
        
        # Get start time for scheduled games
        start_time = ''
        start_time_utc = ''
        if game_status == 'Scheduled':
            start_time_utc = game.get('startTimeUTC', '')
            if start_time_utc:
                try:
                    # Parse UTC time and convert to EST
                    dt_utc = datetime.fromisoformat(start_time_utc.replace('Z', '+00:00'))
                    # Convert to EST (UTC-5)
                    dt_est = dt_utc - timedelta(hours=5)
                    start_time = dt_est.strftime('%I:%M %p')
                except:
                    start_time = start_time_utc
        
        # Get team records
        away_record = ''
        home_record = ''
        if abbrev_to_info:
            away_info = abbrev_to_info.get(away_abbrev, {})
            home_info = abbrev_to_info.get(home_abbrev, {})
            if away_info:
                # Use the 'record' string if available, otherwise build it
                away_record = away_info.get('record', '')
                if not away_record:
                    w = away_info.get('wins', 0)
                    l = away_info.get('losses', 0)
                    ot = away_info.get('otLosses', 0)
                    away_record = f"{w}-{l}-{ot}" if w or l or ot else ''
            if home_info:
                # Use the 'record' string if available, otherwise build it
                home_record = home_info.get('record', '')
                if not home_record:
                    w = home_info.get('wins', 0)
                    l = home_info.get('losses', 0)
                    ot = home_info.get('otLosses', 0)
                    home_record = f"{w}-{l}-{ot}" if w or l or ot else ''
        
        # Get shots on goal
        away_shots = ''
        home_shots = ''
        if game_data and game_status != 'Scheduled':
            try:
                away_shots = game_data.get('awayTeam', {}).get('sog', '')
                home_shots = game_data.get('homeTeam', {}).get('sog', '')
            except:
                pass
        
        # Get scoring summary with team leader indicators (using pre-fetched data)
        scoring_summary = []
        if game_data and game_status != 'Scheduled':
            raw_summary = format_scoring_summary(game_id, game_data)
            
            # Mark leaders in scoring summary using pre-fetched team_top_leaders
            if team_top_leaders:
                for goal in raw_summary:
                    team = goal.get('team', '')
                    scorer_id = goal.get('scorer', {}).get('id')
                    
                    if team in team_top_leaders and scorer_id:
                        goal['scorer']['is_goals_leader'] = scorer_id in team_top_leaders[team].get('goals', [])
                        goal['scorer']['is_assists_leader'] = scorer_id in team_top_leaders[team].get('assists', [])
                        goal['scorer']['is_points_leader'] = scorer_id in team_top_leaders[team].get('points', [])
                    
                    # Mark assists leaders too
                    for assist in goal.get('assists', []):
                        assist_id = assist.get('id')
                        if team in team_top_leaders and assist_id:
                            assist['is_goals_leader'] = assist_id in team_top_leaders[team].get('goals', [])
                            assist['is_assists_leader'] = assist_id in team_top_leaders[team].get('assists', [])
                            assist['is_points_leader'] = assist_id in team_top_leaders[team].get('points', [])
            
            # Group by period for easier template rendering
            period_groups = {}
            for goal in raw_summary:
                period = goal.get('period', 'Unknown')
                if period not in period_groups:
                    period_groups[period] = []
                period_groups[period].append(goal)
            
            # Add all periods, even those with no goals
            # Determine the number of periods based on game status
            total_periods = 3  # Default for regulation
            if game_data:
                current_period = game_data.get('period', game_data.get('currentPeriod', 0))
                if current_period and current_period > 3:
                    total_periods = current_period  # Include OT periods
            
            # Ensure all periods are represented (at least regulation periods)
            for p in range(1, total_periods + 1):
                if p <= 3:
                    period_name = f"Period {p}"
                elif p == 4:
                    period_name = "OT"
                else:
                    period_name = f"OT{p - 3}"
                
                if period_name not in period_groups:
                    period_groups[period_name] = []
            
            # Convert to list of periods with goals (sorted by period number)
            def period_sort_key(item):
                period = item['period']
                if period.startswith('Period'):
                    return int(period.split()[1])
                elif period == 'OT':
                    return 4
                elif period.startswith('OT'):
                    return 3 + int(period[2:])
                elif period == 'SO':
                    return 100
                else:
                    return 999
            
            scoring_summary = [{'period': period, 'goals': goals} for period, goals in period_groups.items()]
            scoring_summary.sort(key=period_sort_key)
        
        # Extract broadcast information from schedule data (more reliable than where-to-watch API)
        where_to_watch = []
        try:
            # Look for broadcast info in the game data
            tv_broadcasts = game.get('tvBroadcasts', []) or []
            broadcast_list = []
            for broadcast in tv_broadcasts:
                network = broadcast.get('network', '')
                market = broadcast.get('market', '')
                country = broadcast.get('countryCode', '')
                
                if network:
                    if country and market:
                        formatted = f"{country} ({market}): {network}"
                    elif country:
                        formatted = f"{country}: {network}"
                    elif market:
                        formatted = f"{market}: {network}"
                    else:
                        formatted = network
                    
                    # Add with sort key: CA=0, US=1, others=2
                    sort_key = 0 if country == 'CA' else (1 if country == 'US' else 2)
                    broadcast_list.append((sort_key, formatted))
            
            # Sort by priority (CA first, then US, then others) and extract formatted strings
            broadcast_list.sort(key=lambda x: x[0])
            where_to_watch = [item[1] for item in broadcast_list]
        except Exception as e:
            sys.stderr.write(f"[WARNING] Failed to extract broadcast info for game {game_id}: {e}\n")
        
        processed_games.append({
            'id': game_id,
            'away_team': {
                'abbrev': away_abbrev,
                'name': away_team.get('name', {}).get('default', away_abbrev),
                'score': away_score,
                'record': away_record,
                'shots': away_shots,
                'logo': get_team_logo_path(away_abbrev)
            },
            'home_team': {
                'abbrev': home_abbrev,
                'name': home_team.get('name', {}).get('default', home_abbrev),
                'score': home_score,
                'record': home_record,
                'shots': home_shots,
                'logo': get_team_logo_path(home_abbrev)
            },
            'status': game_status,
            'period_info': period_info,
            'start_time': start_time,
            'start_time_utc': start_time_utc,
            'scoring_summary': scoring_summary,
            'where_to_watch': where_to_watch
        })
    
    # Sort games: Live/In Progress first, then Scheduled, then Completed at the bottom
    def game_sort_key(game):
        status = game.get('status', '').lower()
        if 'live' in status or 'progress' in status or 'period' in status or 'intermission' in status:
            return 0  # Live games first
        elif 'final' in status or 'official' in status or 'completed' in status:
            return 2  # Completed games last
        else:
            return 1  # Scheduled games in the middle
    
    processed_games.sort(key=game_sort_key)
    
    # Format display date
    try:
        display_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%A, %B %d, %Y')
    except:
        display_date = date_str
    
    # Process leaders data for display (using already-fetched data)
    print(f"[FUNCTION] Processing {len(processed_games)} games for display...")
    leaders = process_leaders_for_display(leaders_data)
    
    func_total = (time.time() - func_start) * 1000
    print(f"[FUNCTION] get_games_data() completed in {func_total:.0f}ms")
    print(f"[FUNCTION] Returning {len(processed_games)} processed games\\n")
    sys.stdout.flush()
    
    return {
        'success': True,
        'games': processed_games,
        'date': date_str,
        'display_date': display_date,
        'count': len(processed_games),
        'leaders': leaders
    }


def process_leaders_for_display(leaders_data):
    """
    Process already-fetched leader data for display.
    Takes the raw API response and formats it for the template.
    """
    result = {
        'goals': [],
        'assists': [],
        'points': []
    }
    
    try:
        for category in ['goals', 'assists', 'points']:
            raw_list = leaders_data.get(category, [])
            # Only take top 25 for display
            for entry in raw_list[:25]:
                player_name = entry.get('playerName', 'Unknown')
                player_id = entry.get('playerId')
                value = entry.get('value', 0)
                team_abbrev = entry.get('teamAbbrev', '')
                
                result[category].append({
                    'id': player_id,
                    'name': player_name,
                    'value': value,
                    'team': team_abbrev
                })
    except Exception:
        pass
    
    return result


def get_league_leaders():
    """
    DEPRECATED: This function is kept for backwards compatibility but is no longer used.
    Leaders are now fetched once in get_games_data() for efficiency.
    """
    try:
        leaders_data = fetch_skater_stat_leaders(categories=['goals', 'assists', 'points'], limit=25)
        
        result = {
            'goals': [],
            'assists': [],
            'points': []
        }
        
        for category in ['goals', 'assists', 'points']:
            raw_list = leaders_data.get(category, [])
            for entry in raw_list:
                player_name = entry.get('playerName', 'Unknown')
                player_id = entry.get('playerId')
                value = entry.get('value', 0)
                team_abbrev = entry.get('teamAbbrev', '')
                
                result[category].append({
                    'id': player_id,
                    'name': player_name,
                    'value': value,
                    'team': team_abbrev
                })
        
        return result
    except Exception as e:
        # Return empty leaders on error
        return {
            'goals': [],
            'assists': [],
            'points': []
        }


# Ensure the script doesn't run automatically when imported by Django
if __name__ == "__main__":
    # Check dependencies (simplified as they will run inside get_score_output too)
    try:
        import requests
    except ImportError:
        print("❌ Error: 'requests' module not found.")
        print("Please install it with: pip install requests")
        sys.exit(1)
    
    main()
else:
    # When imported as a module (by Django), preload startup data
    preload_startup_data()