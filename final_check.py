"""Final verification - show complete GitHub repo tree."""
import urllib.request, json

TOKEN = open(r"C:\Users\Administrator\.gh_token").read().strip()

url = "https://api.github.com/repos/mjy1113451/astrbot_plugin_b-/git/trees/main?recursive=1"
req = urllib.request.Request(url)
req.add_header("Authorization", f"token {TOKEN}")
req.add_header("Accept", "application/vnd.github.v3+json")
req.add_header("User-Agent", "final-check")

data = json.loads(urllib.request.urlopen(req).read())

print(f"=== GitHub repo tree (main branch) - {len(data['tree'])} items ===\n")
for item in data["tree"]:
    t = "📁" if item["type"] == "tree" else "📄"
    print(f"  {t} {item['path']}")
