"""
Shared Sports Configuration

Maps ALL Polymarket series IDs to Kambi sport identifiers.
Uses series_id + tag_id=100639 to fetch only game bets (not futures).

Full list from: GET https://gamma-api.polymarket.com/sports
"""

from dataclasses import dataclass


# Tag ID that filters for game bets only (not futures/outrights)
POLYMARKET_GAME_BETS_TAG_ID = 100639


@dataclass
class SportMapping:
    """Mapping between Polymarket and Kambi sport identifiers."""
    name: str               # Human-readable name
    code: str               # Polymarket sport code
    polymarket_series_id: int  # Polymarket series_id
    kambi_sport: str        # Kambi sport identifier


# ALL 111 sports from Polymarket /sports endpoint
SPORTS_CONFIG = [
    # ========== US Sports ==========
    SportMapping("NBA", "nba", 10345, "basketball"),
    SportMapping("NFL", "nfl", 10187, "american_football"),
    SportMapping("NHL", "nhl", 10346, "ice_hockey"),
    SportMapping("MLB", "mlb", 3, "baseball"),
    SportMapping("WNBA", "wnba", 10105, "basketball"),
    SportMapping("NCAA Basketball", "ncaab", 39, "basketball"),
    SportMapping("College Football", "cfb", 10210, "american_football"),
    SportMapping("College Basketball", "cbb", 10470, "basketball"),
    SportMapping("College Women's Basketball", "cwbb", 10471, "basketball"),
    SportMapping("MLS", "mls", 10189, "football"),
    
    # ========== European Football - Top 5 ==========
    SportMapping("Premier League", "epl", 10188, "football"),
    SportMapping("La Liga", "lal", 10193, "football"),
    SportMapping("Bundesliga", "bun", 10194, "football"),
    SportMapping("Ligue 1", "fl1", 10195, "football"),
    SportMapping("Serie A", "sea", 10203, "football"),
    
    # ========== European Competitions ==========
    SportMapping("Champions League", "ucl", 10204, "football"),
    SportMapping("Europa League", "uel", 10209, "football"),
    SportMapping("Conference League", "col", 10437, "football"),
    SportMapping("EFL Championship", "efl", 10230, "football"),
    SportMapping("FA Cup", "efa", 10307, "football"),
    SportMapping("DFB Pokal", "dfb", 10317, "football"),
    SportMapping("Copa del Rey", "cdr", 10316, "football"),
    SportMapping("Coppa Italia", "itc", 10287, "football"),
    SportMapping("Coupe de France", "cde", 10315, "football"),
    
    # ========== Other European Leagues ==========
    SportMapping("Eredivisie", "ere", 10286, "football"),
    SportMapping("Primeira Liga", "por", 10330, "football"),
    SportMapping("Russian Premier", "rus", 10306, "football"),
    SportMapping("Turkish Super Lig", "tur", 10292, "football"),
    SportMapping("Danish Superliga", "den", 10363, "football"),
    SportMapping("Norwegian Eliteserien", "nor", 10362, "football"),
    SportMapping("Saudi Pro League", "spl", 10361, "football"),
    SportMapping("Spanish Segunda", "ssc", 10863, "football"),
    
    # ========== South American Football ==========
    SportMapping("Argentina Liga", "arg", 10285, "football"),
    SportMapping("Brazil Serie A", "bra", 10359, "football"),
    SportMapping("Liga MX", "mex", 10290, "football"),
    SportMapping("Copa Libertadores", "lib", 10289, "football"),
    SportMapping("Copa Sudamericana", "sud", 10291, "football"),
    SportMapping("Leagues Cup", "lcs", 10288, "football"),
    SportMapping("CONMEBOL", "con", 10246, "football"),
    SportMapping("CONCACAF", "cof", 10244, "football"),
    
    # ========== Asian/Oceania Football ==========
    SportMapping("AFC", "afc", 10241, "football"),
    SportMapping("A-League", "aus", 10438, "football"),
    SportMapping("Chinese Super League", "chi", 10439, "football"),
    SportMapping("Indian Super League", "ind", 10364, "football"),
    SportMapping("J-League", "jap", 10360, "football"),
    SportMapping("J-League 2", "ja2", 10443, "football"),
    SportMapping("K-League", "kor", 10444, "football"),
    SportMapping("OFC", "ofc", 10294, "football"),
    
    # ========== African Football ==========
    SportMapping("CAF", "caf", 10240, "football"),
    SportMapping("Africa Cup of Nations", "acn", 10786, "football"),
    
    # ========== International ==========
    SportMapping("FIFA", "fif", 10238, "football"),
    SportMapping("UEFA", "uef", 10243, "football"),
    
    # ========== Ice Hockey ==========
    SportMapping("KHL", "khl", 10700, "ice_hockey"),
    SportMapping("SHL", "shl", 10695, "ice_hockey"),
    SportMapping("AHL", "ahl", 10699, "ice_hockey"),
    SportMapping("Czech Extraliga", "cehl", 10702, "ice_hockey"),
    SportMapping("DEL", "dehl", 10701, "ice_hockey"),
    SportMapping("Swiss NL", "snhl", 102911, "ice_hockey"),
    
    # ========== Tennis ==========
    SportMapping("ATP", "atp", 10365, "tennis"),
    SportMapping("WTA", "wta", 10366, "tennis"),
    
    # ========== Combat Sports ==========
    SportMapping("MMA/UFC", "mma", 10500, "mma"),
    
    # ========== Basketball - Other ==========
    SportMapping("Basketball CL", "bkcl", 10879, "basketball"),
    SportMapping("ACB Liga Endesa", "bkligend", 10878, "basketball"),
    SportMapping("NBL Australia", "bknbl", 10876, "basketball"),
    SportMapping("Basketball Serie A", "bkseriea", 10877, "basketball"),
    SportMapping("French Pro A", "bkfr1", 10872, "basketball"),
    SportMapping("Argentine LNB", "bkarg", 10873, "basketball"),
    SportMapping("Korean KBL", "bkkbl", 10874, "basketball"),
    SportMapping("Chinese CBA", "bkcba", 10875, "basketball"),
    
    # ========== Baseball - Other ==========
    SportMapping("KBO", "kbo", 10370, "baseball"),
    
    # ========== Cricket ==========
    SportMapping("IPL", "ipl", 44, "cricket"),
    SportMapping("Big Bash", "abb", 10449, "cricket"),
    SportMapping("Cricket International", "crint", 10528, "cricket"),
    SportMapping("ODI", "odi", 10451, "cricket"),
    SportMapping("T20", "t20", 10445, "cricket"),
    SportMapping("Test Cricket", "test", 10661, "cricket"),
    SportMapping("Cricket Australia", "craus", 10752, "cricket"),
    SportMapping("Cricket Bangladesh", "crban", 10799, "cricket"),
    SportMapping("Cricket England", "creng", 10750, "cricket"),
    SportMapping("Cricket India", "crind", 10748, "cricket"),
    SportMapping("Cricket New Zealand", "crnew", 10755, "cricket"),
    SportMapping("Cricket Pakistan", "crpak", 10751, "cricket"),
    SportMapping("Cricket South Africa", "crsou", 10753, "cricket"),
    SportMapping("Cricket UAE", "cruae", 10754, "cricket"),
    SportMapping("CSA", "csa", 10446, "cricket"),
    SportMapping("Sheffield Shield", "she", 10453, "cricket"),
    SportMapping("LPL", "lpl", 10448, "cricket"),
    SportMapping("PSP", "psp", 10447, "cricket"),
    SportMapping("SASA", "sasa", 10450, "cricket"),
    
    # ========== Rugby ==========
    SportMapping("Rugby Premiership", "ruprem", 10840, "rugby"),
    SportMapping("Top 14", "rutopft", 10841, "rugby"),
    SportMapping("European Rugby Champions", "rueuchamp", 10882, "rugby"),
    SportMapping("Six Nations", "rusixnat", 10880, "rugby"),
    SportMapping("URC", "ruurc", 10881, "rugby"),
    SportMapping("Super Rugby", "rusrp", 10883, "rugby"),
    SportMapping("SA Rugby Championship", "ruchamp", 10884, "rugby"),
    
    # ========== Esports ==========
    SportMapping("CS2", "cs2", 10310, "esports"),
    SportMapping("Dota 2", "dota2", 10309, "esports"),
    SportMapping("League of Legends", "lol", 10311, "esports"),
    SportMapping("Valorant", "val", 10369, "esports"),
    SportMapping("Overwatch", "ow", 10430, "esports"),
    SportMapping("FIFA Esports", "fifa", 10428, "esports"),
    SportMapping("Call of Duty", "codmw", 10427, "esports"),
    SportMapping("PUBG", "pubg", 10431, "esports"),
    SportMapping("Rainbow Six", "r6siege", 10432, "esports"),
    SportMapping("Rocket League", "rl", 10433, "esports"),
    SportMapping("StarCraft", "sc", 10436, "esports"),
    SportMapping("StarCraft 2", "sc2", 10435, "esports"),
    SportMapping("Honor of Kings", "hok", 10434, "esports"),
    SportMapping("Mobile Legends", "mlbb", 10426, "esports"),
    SportMapping("Wild Rift", "wildrift", 10429, "esports"),
]


def get_all_sports() -> list[SportMapping]:
    """Get all configured sports."""
    return SPORTS_CONFIG


def get_polymarket_series_ids() -> list[int]:
    """Get list of Polymarket series IDs to fetch."""
    return [s.polymarket_series_id for s in SPORTS_CONFIG]


def get_kambi_sports() -> list[str]:
    """Get unique list of Kambi sports to fetch."""
    return list(set(s.kambi_sport for s in SPORTS_CONFIG))


def get_sport_by_series_id(series_id: int) -> SportMapping | None:
    """Get sport mapping by Polymarket series ID."""
    for s in SPORTS_CONFIG:
        if s.polymarket_series_id == series_id:
            return s
    return None


def get_sport_by_code(code: str) -> SportMapping | None:
    """Get sport mapping by Polymarket code."""
    for s in SPORTS_CONFIG:
        if s.code == code:
            return s
    return None


def get_sports_by_kambi_sport(kambi_sport: str) -> list[SportMapping]:
    """Get all sport mappings for a Kambi sport."""
    return [s for s in SPORTS_CONFIG if s.kambi_sport == kambi_sport]
