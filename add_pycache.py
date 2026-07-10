"""Add __pycache__ folders from source and push."""
import subprocess, os, shutil

repo = r"C:\Users\Administrator\Desktop\astrbot_plugin_b-"
src = r"C:\Users\Administrator\Desktop\bilibili_learning_bot3.0.2\bilibili_learning_bot"
token = open(r"C:\Users\Administrator\.gh_token").read().strip()

# 1. Remove __pycache__ and *.pyc from .gitignore
print("=== Updating .gitignore ===")
gitignore = os.path.join(repo, ".gitignore")
with open(gitignore, "r") as f:
    lines = f.readlines()
new_lines = []
for line in lines:
    stripped = line.strip()
    if stripped in ("__pycache__/", "*.pyc", "*.pyo", "*.pyd"):
        print(f"  Removing: {stripped}")
        continue
    new_lines.append(line)
with open(gitignore, "w") as f:
    f.writelines(new_lines)

# 2. Copy __pycache__ from source for each target folder
folders = ["brain", "core", "persona", "cli", "tests", "knowledge"]
for folder in folders:
    src_pyc = os.path.join(src, folder, "__pycache__")
    dst_pyc = os.path.join(repo, folder, "__pycache__")
    if os.path.exists(src_pyc):
        print(f"  Copying {folder}/__pycache__/")
        if os.path.exists(dst_pyc):
            shutil.rmtree(dst_pyc)
        shutil.copytree(src_pyc, dst_pyc)

# 3. Auth
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                f"https://mjy1113451:{token}@github.com/mjy1113451/astrbot_plugin_b-.git"], check=True)

# 4. Add & commit & push
subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
print("\n=== Status ===")
subprocess.run(["git", "-C", repo, "status", "--short"])
subprocess.run(["git", "-C", repo, "commit", "-m", "add __pycache__ folders"], check=True)
result = subprocess.run(["git", "-C", repo, "push", "origin", "main"],
                        capture_output=True, text=True)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)

subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                "https://github.com/mjy1113451/astrbot_plugin_b-.git"])

print("✅ Done!" if result.returncode == 0 else "❌ Failed")
