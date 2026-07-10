"""Check GitHub repo via API."""
import urllib.request
import json

with open(r"C:\Users\Administrator\.gh_token") as f:
    token = f.read().strip()

# Check repo info
req = urllib.request.Request("https://api.github.com/repos/mjy1113451/astrbot_plugin_b-")
req.add_header("Authorization", f"token {token}")
req.add_header("Accept", "application/vnd.github.v3+json")
req.add_header("User-Agent", "check-script")

data = json.loads(urllib.request.urlopen(req).read())
print(f"Repo: {data['full_name']}")
print(f"Default branch: {data['default_branch']}")
print(f"Size: {data['size']} KB")

# List contents of root
print("\n=== Root files & folders ===")
req2 = urllib.request.Request("https://api.github.com/repos/mjy1113451/astrbot_plugin_b-/contents/")
req2.add_header("Authorization", f"token {token}")
req2.add_header("Accept", "application/vnd.github.v3+json")
req2.add_header("User-Agent", "check-script")

items = json.loads(urllib.request.urlopen(req2).read())
for item in items:
    t = "📁" if item["type"] == "dir" else "📄"
    print(f"  {t} {item['name']}")

# Check specific folders
for folder in ["brain", "core", "persona", "cli", "tests", "model", "knowledge"]:
    url = f"https://api.github.com/repos/mjy1113451/astrbot_plugin_b-/contents/{folder}"
    req3 = urllib.request.Request(url)
    req3.add_header("Authorization", f"token {token}")
    req3.add_header("Accept", "application/vnd.github.v3+json")
    req3.add_header("User-Agent", "check-script")
    try:
        items = json.loads(urllib.request.urlopen(req3).read())
        print(f"\n  {folder}/ ({len(items)} files)")
    except Exception as e:
        print(f"\n  {folder}/: ERROR - {e}")
