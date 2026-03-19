"""
VK Vote Audit — Step 1: Collect data
Fetches poll results, voter lists, user profiles, and group members via VK API.
Saves raw data to output/audit_data.json for analysis.
"""
import urllib.request, json, ssl, sys, time, os
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Config from .env
# ---------------------------------------------------------------------------
def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env()

TOKEN = os.environ.get("VK_TOKEN")
if not TOKEN:
    print("ERROR: VK_TOKEN not set. Copy .env.example to .env and fill in your token.")
    sys.exit(1)

WALL_POST = os.environ.get("VK_WALL_POST", "-167291581_464")
owner_id, post_id = WALL_POST.split("_")
OWNER_ID = int(owner_id)
POST_ID = int(post_id)
GROUP_ID = int(os.environ.get("VK_GROUP_ID", str(abs(OWNER_ID))))
TARGET_CITY = os.environ.get("VK_TARGET_CITY", "киров").lower()

V = "5.199"
CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
CTX.maximum_version = ssl.TLSVersion.TLSv1_2

# ---------------------------------------------------------------------------
# VK API helper
# ---------------------------------------------------------------------------
def api(method, **params):
    params["access_token"] = TOKEN
    params["v"] = V
    url = f"https://api.vk.com/method/{method}"
    body = "&".join(f"{k}={v}" for k, v in params.items()).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, context=CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        msg = data["error"].get("error_msg", data["error"])
        print(f"  API Error in {method}: {msg}")
        return None
    return data.get("response")

# ---------------------------------------------------------------------------
# Step 1: Get poll from wall post
# ---------------------------------------------------------------------------
print("=" * 60)
print("STEP 1: Getting poll data...")
post_data = api("wall.getById", posts=f"{OWNER_ID}_{POST_ID}")
if not post_data or not post_data.get("items"):
    print("ERROR: Could not fetch wall post. Check VK_WALL_POST and VK_TOKEN.")
    sys.exit(1)

post = post_data["items"][0]
poll = None
for att in post.get("attachments", []):
    if att["type"] == "poll":
        poll = att["poll"]
        break

if not poll:
    print("ERROR: No poll found in this post!")
    sys.exit(1)

if poll.get("anonymous"):
    print("WARNING: Poll is anonymous — voter lists are not available.")
    print("Only limited analysis is possible.")

poll_id = poll["id"]
poll_owner = poll["owner_id"]
total_votes = poll["votes"]
created = datetime.fromtimestamp(poll["created"], tz=timezone.utc)
answers = poll["answers"]

print(f'  Poll: {poll["question"]}')
print(f"  Created: {created}")
print(f"  Total votes: {total_votes}")
print(f"  Anonymous: {poll.get('anonymous', False)}")
print()

for a in answers:
    print(f'  [{a["id"]}] {a["text"]}: {a["votes"]} votes ({a["rate"]}%)')

# ---------------------------------------------------------------------------
# Step 2: Get voters per answer
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("STEP 2: Getting voters for each answer...")

answer_voters = {}

for a in answers:
    aid = a["id"]
    voters = []
    offset = 0

    while True:
        result = api(
            "polls.getVoters",
            poll_id=poll_id,
            owner_id=poll_owner,
            answer_ids=aid,
            count=1000,
            offset=offset,
            fields="",
        )
        if not result:
            break

        users_data = result[0].get("users", {})
        items = users_data.get("items", [])
        total = users_data.get("count", 0)

        voters.extend(items)
        offset += 1000

        if offset >= total or not items:
            break
        time.sleep(0.34)

    answer_voters[aid] = voters
    print(f'  {a["text"]}: {len(voters)} voter IDs (expected {a["votes"]})')
    time.sleep(0.34)

all_voter_ids = []
for vlist in answer_voters.values():
    all_voter_ids.extend(vlist)

print(f"\n  Total unique voters: {len(set(all_voter_ids))}")

# ---------------------------------------------------------------------------
# Step 3: Get user profiles
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("STEP 3: Getting user profiles...")

FIELDS = "city,country,last_seen,photo_id,counters,online,sex,bdate,deactivated,is_closed"
unique_ids = list(set(all_voter_ids))
profiles = {}

for i in range(0, len(unique_ids), 1000):
    batch = unique_ids[i : i + 1000]
    ids_str = ",".join(str(x) for x in batch)
    result = api("users.get", user_ids=ids_str, fields=FIELDS)
    if result:
        for u in result:
            profiles[u["id"]] = u
    print(f"  Profiles: {min(i + 1000, len(unique_ids))}/{len(unique_ids)}")
    time.sleep(0.34)

print(f"  Total profiles: {len(profiles)}")

# ---------------------------------------------------------------------------
# Step 4: Get group members
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("STEP 4: Getting group members...")

members = set()
offset = 0
while True:
    result = api("groups.getMembers", group_id=GROUP_ID, count=1000, offset=offset)
    if not result:
        break
    items = result.get("items", [])
    members.update(items)
    total_members = result.get("count", 0)
    offset += 1000
    if offset >= total_members or not items:
        break
    time.sleep(0.34)

print(f"  Group members: {len(members)}")

# ---------------------------------------------------------------------------
# Step 5: Save
# ---------------------------------------------------------------------------
os.makedirs("output", exist_ok=True)

output = {
    "poll": poll,
    "answer_voters": {str(k): v for k, v in answer_voters.items()},
    "profiles": {str(k): v for k, v in profiles.items()},
    "group_members": list(members),
    "group_members_count": len(members),
    "target_city": TARGET_CITY,
    "wall_post": f"{OWNER_ID}_{POST_ID}",
    "group_id": GROUP_ID,
    "timestamp": datetime.now().isoformat(),
}

out_path = "output/audit_data.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=1)

print(f"\nData saved to {out_path}")
print("DONE. Now run: python report.py")
