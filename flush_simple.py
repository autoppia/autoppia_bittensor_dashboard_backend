#!/usr/bin/env python3
"""
Simple script to flush (reset) the PostgreSQL database.
This is a wrapper that calls the bash script truncate_all_tables.sh
"""

import subprocess
import sys
from pathlib import Path

def main():
    script_path = Path(__file__).parent / "scripts" / "bash" / "truncate_all_tables.sh"
    
    if not script_path.exists():
        print(f"❌ Script not found: {script_path}")
        sys.exit(1)
    
    print("🔄 Flushing database using truncate_all_tables.sh...")
    result = subprocess.run(["bash", str(script_path)], cwd=script_path.parent.parent.parent)
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()
