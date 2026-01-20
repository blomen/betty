
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(os.path.join(str(Path(__file__).parent.parent), "backend"))
sys.stdout.reconfigure(encoding='utf-8')

from src.utils.matching import normalize_team_name

def test_normalization():
    print("Checking Team Normalization...")
    
    cases = [
        ("Athletic Bilbao", "athletic club"),
        ("Athletic Club", "athletic club"),
        ("Olympiacos SFP", "olympiacos"),
        ("Bayer 04 Leverkusen", "bayer leverkusen"),
        ("Sevilla FC", "sevilla"), # implied by suffix removal
        ("Sevilla", "sevilla"),
    ]
    
    failed = 0
    for input_name, expected in cases:
        result = normalize_team_name(input_name)
        if result == expected:
            print(f" [PASS] '{input_name}' -> '{result}'")
        else:
            print(f" [FAIL] '{input_name}' -> '{result}' (Expected: '{expected}')")
            failed += 1
            
    if failed == 0:
        print("\nAll checks passed!")
    else:
        print(f"\n{failed} checks failed.")
        sys.exit(1)

if __name__ == "__main__":
    test_normalization()
