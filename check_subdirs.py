"""Check local subdirectories vs GitHub."""
import urllib.request, json, os

TOKEN = open(r"C:\Users\Administrator\.gh_token").read().strip()
REPO = r"C:\Users\Administrator\Desktop\astrbot_plugin_b-"

# Get local directories
print("=== Local directories ===")
for root, dirs, files in os.walk(REPO):
    dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', '.pytest_cache']]
    rel = os.path.relpath(root, REPO)
    if rel == '.':
        continue
    depth = rel.count(os.sep)
    indent = "  " * depth
    print(f"{indent}📁 {rel}/ ({len(files)} files)")

# Get remote tree recursively
print("\n=== Remote tree (GitHub API) ===")
def fetch_tree(path=""):
    url = f"https://api.github.com/repos/mjy1113451/astrbot_plugin_b-/contents/{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {TOKEN}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "check")
    try:
        items = json.loads(urllib.request.urlopen(req).read())
        if isinstance(items, dict):
            # It's a file, not a directory
            return
        for item in items:
            indent = "  " * path.count('/') if path else ""
            t = "📁" if item["type"] == "dir" else "📄"
            print(f"{indent}  {t} {item['name']}")
            if item["type"] == "dir":
                subpath = f"{path}/{item['name']}" if path else item["name"]
                fetch_tree(subpath)
    except Exception as e:
        print(f"  Error: {e}")

fetch_tree()
