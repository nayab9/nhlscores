"""
Download NHL team logos locally for faster loading
Run this script once: python download_logos.py
"""

import requests
import os

# NHL team abbreviations and their logo URLs from NHL.com CDN
NHL_TEAMS = {
    'ANA': 'https://assets.nhle.com/logos/nhl/svg/ANA_light.svg',
    'BOS': 'https://assets.nhle.com/logos/nhl/svg/BOS_light.svg',
    'BUF': 'https://assets.nhle.com/logos/nhl/svg/BUF_light.svg',
    'CAR': 'https://assets.nhle.com/logos/nhl/svg/CAR_light.svg',
    'CBJ': 'https://assets.nhle.com/logos/nhl/svg/CBJ_light.svg',
    'CGY': 'https://assets.nhle.com/logos/nhl/svg/CGY_light.svg',
    'CHI': 'https://assets.nhle.com/logos/nhl/svg/CHI_light.svg',
    'COL': 'https://assets.nhle.com/logos/nhl/svg/COL_light.svg',
    'DAL': 'https://assets.nhle.com/logos/nhl/svg/DAL_light.svg',
    'DET': 'https://assets.nhle.com/logos/nhl/svg/DET_light.svg',
    'EDM': 'https://assets.nhle.com/logos/nhl/svg/EDM_light.svg',
    'FLA': 'https://assets.nhle.com/logos/nhl/svg/FLA_light.svg',
    'LAK': 'https://assets.nhle.com/logos/nhl/svg/LAK_light.svg',
    'MIN': 'https://assets.nhle.com/logos/nhl/svg/MIN_light.svg',
    'MTL': 'https://assets.nhle.com/logos/nhl/svg/MTL_light.svg',
    'NJD': 'https://assets.nhle.com/logos/nhl/svg/NJD_light.svg',
    'NSH': 'https://assets.nhle.com/logos/nhl/svg/NSH_light.svg',
    'NYI': 'https://assets.nhle.com/logos/nhl/svg/NYI_light.svg',
    'NYR': 'https://assets.nhle.com/logos/nhl/svg/NYR_light.svg',
    'OTT': 'https://assets.nhle.com/logos/nhl/svg/OTT_light.svg',
    'PHI': 'https://assets.nhle.com/logos/nhl/svg/PHI_light.svg',
    'PIT': 'https://assets.nhle.com/logos/nhl/svg/PIT_light.svg',
    'SEA': 'https://assets.nhle.com/logos/nhl/svg/SEA_light.svg',
    'SJS': 'https://assets.nhle.com/logos/nhl/svg/SJS_light.svg',
    'STL': 'https://assets.nhle.com/logos/nhl/svg/STL_light.svg',
    'TBL': 'https://assets.nhle.com/logos/nhl/svg/TBL_light.svg',
    'TOR': 'https://assets.nhle.com/logos/nhl/svg/TOR_light.svg',
    'UTA': 'https://assets.nhle.com/logos/nhl/svg/UTA_light.svg',
    'VAN': 'https://assets.nhle.com/logos/nhl/svg/VAN_light.svg',
    'VGK': 'https://assets.nhle.com/logos/nhl/svg/VGK_light.svg',
    'WPG': 'https://assets.nhle.com/logos/nhl/svg/WPG_light.svg',
    'WSH': 'https://assets.nhle.com/logos/nhl/svg/WSH_light.svg',
}

def download_logos():
    """Download all NHL team logos to static/team_logos/"""
    logo_dir = os.path.join(os.path.dirname(__file__), 'static', 'team_logos')
    os.makedirs(logo_dir, exist_ok=True)
    
    print(f"Downloading {len(NHL_TEAMS)} team logos to {logo_dir}...")
    
    success_count = 0
    for team_abbrev, url in NHL_TEAMS.items():
        filename = f"{team_abbrev}.svg"
        filepath = os.path.join(logo_dir, filename)
        
        try:
            print(f"Downloading {team_abbrev}...", end=' ')
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            print(f"✓ ({len(response.content)} bytes)")
            success_count += 1
        except Exception as e:
            print(f"✗ Error: {e}")
    
    print(f"\nCompleted: {success_count}/{len(NHL_TEAMS)} logos downloaded successfully")

if __name__ == '__main__':
    download_logos()
