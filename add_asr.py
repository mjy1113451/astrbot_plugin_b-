"""Add empty model/asr/ folder and push."""
import subprocess, os

repo = r"C:\Users\Administrator\Desktop\astrbot_plugin_b-"
token = open(r"C:\Users\Administrator\.gh_token").read().strip()

# Create model/asr directory and add .gitkeep
asr_dir = os.path.join(repo, "model", "asr")
os.makedirs(asr_dir, exist_ok=True)
with open(os.path.join(asr_dir, ".gitkeep"), "w") as f:
    pass
print("Created model/asr/.gitkeep")

# Auth
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                f"https://mjy1113451:{token}@github.com/mjy1113451/astrbot_plugin_b-.git"], check=True)

# Add, commit, push
subprocess.run(["git", "-C", repo, "add", "model/asr/.gitkeep"])
subprocess.run(["git", "-C", repo, "commit", "-m", "add model/asr directory"])
result = subprocess.run(["git", "-C", repo, "push", "origin", "main"], capture_output=True, text=True)

print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)

# Clean
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                "https://github.com/mjy1113451/astrbot_plugin_b-.git"])

print("✅ Done!" if result.returncode == 0 else f"❌ Failed: {result.returncode}")
