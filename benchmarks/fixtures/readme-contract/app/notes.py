import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("command", choices=["add", "list"])
p.add_argument("--file", required=True)
p.add_argument("--text")
a = p.parse_args()
f = Path(a.file)
items = json.loads(f.read_text()) if f.exists() else []
if a.command == "add":
    if not a.text or not a.text.strip(): p.error("--text must not be blank")
    items.append(a.text)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(items, ensure_ascii=False))
print(json.dumps(items, ensure_ascii=False))
