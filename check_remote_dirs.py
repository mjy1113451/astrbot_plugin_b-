"""Check what directories exist on GitHub for each target folder."""
import urllib.request, json

TOKEN = open(r"C:\Users\Administrator\.gh_token").read().strip()

def list_dir(path):
    url = f"https://api.github.com/repos/mjy1113451/astrbot_plugin_b-/contents/{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {TOKEN}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "check")
    try:
        items = json.loads(urllib.request.urlopen(req).read())
        if isinstance(items, dict):
            print(f"  ⚠ {path} is a FILE, not a directory: {items['name']}")
            return
        dirs = [i for i in items if i["type"] == "dir"]
        files = [i for i in items if i["type"] == "file"]
        print(f"  📁 Folders ({len(dirs)}): {[d['name'] for d in dirs]}")
        print(f"  📄 Files ({len(files)}): {[f['name'] for f in files]}")
        return dirs, files
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return [], []

for folder in ["brain", "core", "persona", "cli", "tests", "model", "knowledge"]:
    print(f"\n=== {folder}/ ===")
    dirs, files = list_dir(folder)
    # Check subdirs
    for d in dirs:
        subpath = f"{folder}/{d['name']}"
        print(f"\n  === {subpath}/ ===")
        list_dir(subpath)
