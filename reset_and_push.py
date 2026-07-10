"""Complete reset: delete everything, create fresh repo, push ONLY our files to main."""
import subprocess, os, shutil

repo = r"C:\Users\Administrator\Desktop\astrbot_plugin_b-"
src = r"C:\Users\Administrator\Desktop\bilibili_learning_bot3.0.2\bilibili_learning_bot"
token = open(r"C:\Users\Administrator\.gh_token").read().strip()

# 1. Delete everything except our target folders
print("=== Cleaning repo ===")
keep = {'.git', 'brain', 'core', 'persona', 'cli', 'tests', 'model', 'knowledge', '.gitignore'}
for item in os.listdir(repo):
    if item not in keep:
        path = os.path.join(repo, item)
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"  Removed dir: {item}")
        else:
            os.remove(path)
            print(f"  Removed file: {item}")

# Also remove extra files inside our folders that don't belong
# (like .gitkeep files from the old merge)
for root, dirs, files in os.walk(repo):
    if '.git' in root:
        continue
    for f in files:
        if f == '.gitkeep':
            os.remove(os.path.join(root, f))
            print(f"  Removed .gitkeep: {os.path.relpath(root, repo)}/{f}")

# 2. Make sure model/asr exists with .gitkeep
os.makedirs(os.path.join(repo, 'model', 'asr'), exist_ok=True)
with open(os.path.join(repo, 'model', 'asr', '.gitkeep'), 'w') as f:
    pass

# 3. Auth
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                f"https://mjy1113451:{token}@github.com/mjy1113451/astrbot_plugin_b-.git"], check=True)

# 4. Add everything
subprocess.run(["git", "-C", repo, "add", "-A"], check=True)

# 5. Check status
print("\n=== Git status ===")
subprocess.run(["git", "-C", repo, "status", "--short"])

# 6. Commit
print("\n=== Committing ===")
subprocess.run(["git", "-C", repo, "commit", "-m", "Clean push: brain/core/persona/cli/tests/model/knowledge + .gitignore"], check=True)

# 7. Force push to main
print("\n=== Force pushing to main ===")
result = subprocess.run(["git", "-C", repo, "push", "-f", "origin", "main"],
                        capture_output=True, text=True)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)

# Clean
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                "https://github.com/mjy1113451/astrbot_plugin_b-.git"])

print("\n✅ Done!" if result.returncode == 0 else f"\n❌ Failed: {result.returncode}")
