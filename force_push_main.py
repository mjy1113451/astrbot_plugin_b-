"""Force push everything cleanly to main, delete master branch."""
import subprocess

repo = r"C:\Users\Administrator\Desktop\astrbot_plugin_b-"
token = open(r"C:\Users\Administrator\.gh_token").read().strip()

# Auth
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                f"https://mjy1113451:{token}@github.com/mjy1113451/astrbot_plugin_b-.git"], check=True)

# Fetch latest
subprocess.run(["git", "-C", repo, "fetch", "origin"], check=True)

# Checkout main and make sure we're on it
subprocess.run(["git", "-C", repo, "checkout", "main"], check=True)

# Force push main
print("=== Force pushing to main ===")
result = subprocess.run(["git", "-C", repo, "push", "-f", "origin", "main"],
                        capture_output=True, text=True)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)

# Delete remote master
print("\n=== Deleting remote master ===")
result2 = subprocess.run(["git", "-C", repo, "push", "origin", "--delete", "master"],
                         capture_output=True, text=True)
print("STDOUT:", result2.stdout)
print("STDERR:", result2.stderr)

# Clean
subprocess.run(["git", "-C", repo, "remote", "set-url", "origin",
                "https://github.com/mjy1113451/astrbot_plugin_b-.git"])

print("\n✅ Done! Visit: https://github.com/mjy1113451/astrbot_plugin_b-")
