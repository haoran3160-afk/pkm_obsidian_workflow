import json
from pathlib import Path

data = json.loads((Path(__file__).parent / "ruff_json.txt").read_text(encoding="utf-8-sig"))
for e in data:
    fname = e["filename"].replace("\\", "/").split("/")[-1]
    row = e["location"]["row"]
    code = e["code"]
    msg = e["message"]
    print(f"{fname}:{row} [{code}] {msg}")
