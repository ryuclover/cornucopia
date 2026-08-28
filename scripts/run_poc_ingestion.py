from pathlib import Path
from src.ingestion.mql5_adapter import MQL5SignalsIngestionAdapter

if __name__ == "__main__":
    adapter = MQL5SignalsIngestionAdapter()
    raw_dir = Path("data/raw")
    out_dir = Path("data/normalized")
    res = adapter.process_directory(raw_dir, out_dir)
    print("Ingestion Result:", res)
