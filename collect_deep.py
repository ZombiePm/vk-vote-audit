"""
VK Vote Audit — Step 2 (optional): Deep OSINT collection
Fetches friend lists and group subscriptions for suspect accounts.
Saves to output/deep_data.json.

Run AFTER collect.py:
  python collect.py
  python collect_deep.py
  python report.py
"""
import urllib.request, urllib.parse, json, ssl, sys, time, os
from datetime import datetime, timezone
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Config (same as collect.py)
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
    print("ERROR: VK_TOKEN not set.")
    sys.exit(1)

V = "5.199"
CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
CTX.maximum_version = ssl.TLSVersion.TLSv1_2

DATA_PATH = "output/audit_data.json"
if not os.path.exists(DATA_PATH):
    print(f"ERROR: {DATA_PATH} not found. Run collect.py first.")
    sys.exit(1)

with open(DATA_PATH, encoding="utf-8") as f:
    data = json.load(f)

# ---------------------------------------------------------------------------
# VK API helpers
# ---------------------------------------------------------------------------
def api(method, retries=3, **params):
    params["access_token"] = TOKEN
    params["v"] = V
    url = f"https://api.vk.com/method/{method}"
    body = urllib.parse.urlencode(params).encode("utf-8")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            with urllib.request.urlopen(req, context=CTX) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if "error" in result:
                msg = result["error"].get("error_msg", result["error"])
                print(f"  API Error in {method}: {msg}")
                return None
            return result.get("response")
        except (urllib.error.URLError, ConnectionError, ssl.SSLError) as e:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            print(f"  Network error in {method}: {e}")
            return None


def vk_execute(code):
    """Run VKScript via execute method (up to 25 API calls per request)."""
    return api("execute", code=code)


# ---------------------------------------------------------------------------
# Registration date estimation (no API needed)
# ---------------------------------------------------------------------------
# VK user IDs are roughly sequential. Known milestones:
REG_MILESTONES = [
    (1,         2006),
    (10000000,  2009),
    (50000000,  2010),
    (100000000, 2011),
    (150000000, 2012),
    (200000000, 2013),
    (250000000, 2014),
    (300000000, 2015),
    (350000000, 2016),
    (400000000, 2017),
    (450000000, 2018),
    (500000000, 2019),
    (550000000, 2020),
    (600000000, 2021),
    (650000000, 2022),
    (700000000, 2023),
    (750000000, 2024),
    (800000000, 2025),
    (850000000, 2026),
]


def estimate_reg_year(user_id):
    uid = int(user_id)
    if uid <= REG_MILESTONES[0][0]:
        return REG_MILESTONES[0][1]
    if uid >= REG_MILESTONES[-1][0]:
        return REG_MILESTONES[-1][1]
    for i in range(len(REG_MILESTONES) - 1):
        id_lo, yr_lo = REG_MILESTONES[i]
        id_hi, yr_hi = REG_MILESTONES[i + 1]
        if id_lo <= uid < id_hi:
            frac = (uid - id_lo) / (id_hi - id_lo)
            return round(yr_lo + frac * (yr_hi - yr_lo), 1)
    return 2025


# ---------------------------------------------------------------------------
# Scoring (mirrored from report.py)
# ---------------------------------------------------------------------------
def score_voter(uid_s, profiles, group_members, target_city):
    p = profiles.get(str(uid_s), {})
    score = 0

    if p.get("deactivated"):
        score += 3
    if not p.get("photo_id"):
        score += 1
    if int(uid_s) not in group_members:
        score += 1
    city = p.get("city", {})
    if not city:
        score += 1
    elif target_city not in city.get("title", "").lower():
        score += 1
    if p.get("is_closed") and not p.get("photo_id"):
        score += 1
    ls = p.get("last_seen", {})
    if ls:
        ls_time = ls.get("time", 0)
        if ls_time > 0 and (time.time() - ls_time) > 180 * 86400:
            score += 2
    if not ls and not p.get("deactivated"):
        score += 1
    return score


# ---------------------------------------------------------------------------
# Identify suspects
# ---------------------------------------------------------------------------
profiles = data["profiles"]
group_members = set(data.get("group_members", []))
target_city = data.get("target_city", "киров").lower()
answer_voters = data["answer_voters"]
answers = data["poll"]["answers"]

all_suspects = []  # (uid, answer_name, score)
for a in answers:
    aid = str(a["id"])
    for uid in answer_voters.get(aid, []):
        sc = score_voter(str(uid), profiles, group_members, target_city)
        if sc >= 4:
            all_suspects.append((uid, a["text"], sc))

suspect_ids = [s[0] for s in all_suspects]
suspect_set = set(suspect_ids)

print(f"Suspects (score>=4): {len(suspect_ids)}")

# ---------------------------------------------------------------------------
# Step 1: Collect friend lists via execute batches
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("STEP 1: Collecting friend lists for suspects...")

friend_data = {}  # uid -> list of friend IDs
batch_size = 25
suspect_list = list(suspect_set)

for i in range(0, len(suspect_list), batch_size):
    batch = suspect_list[i : i + batch_size]
    ids_js = ",".join(str(x) for x in batch)

    code = f"""
    var ids = [{ids_js}];
    var results = [];
    var i = 0;
    while (i < ids.length) {{
        var r = API.friends.get({{"user_id": ids[i]}});
        results.push({{"id": ids[i], "friends": r}});
        i = i + 1;
    }}
    return results;
    """

    result = vk_execute(code)
    if result:
        for item in result:
            uid = item.get("id")
            friends = item.get("friends")
            if friends and isinstance(friends, dict):
                friend_data[str(uid)] = friends.get("items", [])
            else:
                friend_data[str(uid)] = []

    done = min(i + batch_size, len(suspect_list))
    print(f"  Friends: {done}/{len(suspect_list)}")
    time.sleep(0.4)

print(f"  Got friend lists for {len(friend_data)} suspects")

# ---------------------------------------------------------------------------
# Step 2: Find friend clusters among suspects
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("STEP 2: Analyzing friend clusters...")

# Find pairs of suspects who are friends
suspect_friend_pairs = []
suspect_ids_set = set(str(x) for x in suspect_set)

for uid_s, friends in friend_data.items():
    for fid in friends:
        if str(fid) in suspect_ids_set and int(uid_s) < fid:
            suspect_friend_pairs.append((int(uid_s), fid))

print(f"  Suspect-to-suspect friend pairs: {len(suspect_friend_pairs)}")

# Union-Find for cluster detection
parent = {}

def find(x):
    while parent.get(x, x) != x:
        parent[x] = parent.get(parent[x], parent[x])
        x = parent[x]
    return x

def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb

for a, b in suspect_friend_pairs:
    union(a, b)

# Build clusters
from collections import defaultdict
clusters_map = defaultdict(list)
for uid in suspect_set:
    root = find(uid)
    clusters_map[root].append(uid)

# Only keep clusters with 2+ members
clusters = sorted(
    [c for c in clusters_map.values() if len(c) >= 2],
    key=len,
    reverse=True,
)

print(f"  Clusters (2+ members): {len(clusters)}")
if clusters:
    print(f"  Largest cluster: {len(clusters[0])} suspects")
    total_in_clusters = sum(len(c) for c in clusters)
    print(f"  Total suspects in clusters: {total_in_clusters}")

# ---------------------------------------------------------------------------
# Step 3: Collect group subscriptions via execute batches
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("STEP 3: Collecting group subscriptions for suspects...")

group_data = {}  # uid -> list of group IDs

for i in range(0, len(suspect_list), batch_size):
    batch = suspect_list[i : i + batch_size]
    ids_js = ",".join(str(x) for x in batch)

    code = f"""
    var ids = [{ids_js}];
    var results = [];
    var i = 0;
    while (i < ids.length) {{
        var r = API.groups.get({{"user_id": ids[i], "count": 200}});
        results.push({{"id": ids[i], "groups": r}});
        i = i + 1;
    }}
    return results;
    """

    result = vk_execute(code)
    if result:
        for item in result:
            uid = item.get("id")
            groups = item.get("groups")
            if groups and isinstance(groups, dict):
                group_data[str(uid)] = groups.get("items", [])
            else:
                group_data[str(uid)] = []

    done = min(i + batch_size, len(suspect_list))
    print(f"  Groups: {done}/{len(suspect_list)}")
    time.sleep(0.4)

print(f"  Got group lists for {len(group_data)} suspects")

# ---------------------------------------------------------------------------
# Step 4: Find common groups
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("STEP 4: Analyzing common groups...")

group_counter = Counter()
for uid_s, gids in group_data.items():
    for gid in gids:
        group_counter[gid] += 1

# Groups shared by 3+ suspects
common_groups = [
    {"group_id": gid, "suspect_count": cnt}
    for gid, cnt in group_counter.most_common()
    if cnt >= 3
]

print(f"  Groups shared by 3+ suspects: {len(common_groups)}")

# Fetch names for top common groups
if common_groups:
    top_gids = [str(g["group_id"]) for g in common_groups[:100]]
    gids_str = ",".join(top_gids)
    result = api("groups.getById", group_ids=gids_str, fields="members_count")
    time.sleep(0.34)

    group_info = {}
    if result:
        # Handle both old and new API response formats
        items = result if isinstance(result, list) else result.get("groups", result.get("items", []))
        for g in items:
            group_info[g["id"]] = {
                "name": g.get("name", "?"),
                "screen_name": g.get("screen_name", ""),
                "members_count": g.get("members_count", 0),
            }

    for g in common_groups:
        info = group_info.get(g["group_id"], {})
        g["name"] = info.get("name", f"club{g['group_id']}")
        g["screen_name"] = info.get("screen_name", f"club{g['group_id']}")
        g["members_count"] = info.get("members_count", 0)

    if common_groups:
        print(f"  Top shared group: {common_groups[0]['name']} ({common_groups[0]['suspect_count']} suspects)")

# ---------------------------------------------------------------------------
# Step 5: Build suspect-answer mapping for clusters
# ---------------------------------------------------------------------------
suspect_answer = {}
for uid, aname, sc in all_suspects:
    suspect_answer[uid] = aname

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
output = {
    "friend_data": {str(k): v for k, v in friend_data.items()},
    "suspect_friend_pairs": suspect_friend_pairs,
    "clusters": [[int(x) for x in c] for c in clusters],
    "group_data": {str(k): v for k, v in group_data.items()},
    "common_groups": common_groups,
    "suspect_answer": {str(k): v for k, v in suspect_answer.items()},
    "suspect_scores": {str(s[0]): s[2] for s in all_suspects},
    "reg_milestones": REG_MILESTONES,
    "timestamp": datetime.now().isoformat(),
}

out_path = "output/deep_data.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=1)

print(f"\nDeep data saved to {out_path}")
print("DONE. Now run: python report.py")
