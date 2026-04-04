import json

data = json.load(open('ruff_json.txt', encoding='utf-8'))
for e in data:
    fname = e['filename'].replace('\\', '/').split('/')[-1]
    row = e['location']['row']
    code = e['code']
    msg = e['message']
    print(f"{fname}:{row} [{code}] {msg}")
