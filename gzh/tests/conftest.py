import sys
from pathlib import Path

# ensure repo-root `gzh` package importable without install during dev
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
