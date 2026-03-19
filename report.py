"""
VK Vote Audit — Step 2: Generate HTML report
Reads output/audit_data.json and produces output/report.html
"""
import json, sys, time, os
from datetime import datetime, timezone
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_PATH = "output/audit_data.json"
if not os.path.exists(DATA_PATH):
    print(f"ERROR: {DATA_PATH} not found. Run collect.py first.")
    sys.exit(1)

with open(DATA_PATH, encoding="utf-8") as f:
    data = json.load(f)

poll = data["poll"]
answer_voters = data["answer_voters"]
profiles = data["profiles"]
group_members = set(data.get("group_members", []))
group_members_count = data["group_members_count"]
target_city = data.get("target_city", "киров").lower()
wall_post = data.get("wall_post", "")
group_id = data.get("group_id", 0)
answers = poll["answers"]
total_votes = poll["votes"]
poll_created = datetime.fromtimestamp(poll["created"], tz=timezone.utc)

# Reconstruct URLs
if wall_post:
    owner_id, post_id = wall_post.split("_")
    post_url = f"https://vk.com/wall{wall_post}"
else:
    post_url = ""

# ============================================================
# Scoring
# ============================================================
def score_voter(uid, profiles, group_members, target_city):
    p = profiles.get(str(uid), {})
    score = 0
    reasons = []

    if p.get("deactivated"):
        score += 3
        reasons.append("деактивирован")

    if not p.get("photo_id"):
        score += 1
        reasons.append("нет аватарки")

    if int(uid) not in group_members:
        score += 1
        reasons.append("не в группе")

    city = p.get("city", {})
    if not city:
        score += 1
        reasons.append("город не указан")
    elif target_city not in city.get("title", "").lower():
        score += 1
        reasons.append(f'город: {city.get("title", "?")}')

    if p.get("is_closed") and not p.get("photo_id"):
        score += 1
        reasons.append("закрытый + без фото")

    ls = p.get("last_seen", {})
    if ls:
        ls_time = ls.get("time", 0)
        if ls_time > 0 and (time.time() - ls_time) > 180 * 86400:
            score += 2
            reasons.append("неактивен >180д")

    if not ls and not p.get("deactivated"):
        score += 1
        reasons.append("нет данных активности")

    return score, reasons


# ============================================================
# Analyze each answer
# ============================================================
answer_stats = {}

for a in answers:
    aid = str(a["id"])
    voters = answer_voters.get(aid, [])
    stats = {
        "name": a["text"],
        "total": a["votes"],
        "rate": a["rate"],
        "voters": [],
        "deactivated": 0,
        "no_photo": 0,
        "no_city": 0,
        "wrong_city": 0,
        "closed": 0,
        "inactive": 0,
        "not_member": 0,
        "suspect_count": 0,
        "bot_count": 0,
        "clean_count": 0,
        "cities": Counter(),
    }

    for uid in voters:
        uid_s = str(uid)
        sc, reasons = score_voter(uid_s, profiles, group_members, target_city)
        p = profiles.get(uid_s, {})

        voter_info = {
            "id": uid,
            "name": f"{p.get('first_name', '')} {p.get('last_name', '')}",
            "score": sc,
            "reasons": reasons,
            "deactivated": bool(p.get("deactivated")),
        }
        stats["voters"].append(voter_info)

        if p.get("deactivated"):
            stats["deactivated"] += 1
        if not p.get("photo_id"):
            stats["no_photo"] += 1
        if p.get("is_closed"):
            stats["closed"] += 1
        if int(uid) not in group_members:
            stats["not_member"] += 1

        city = p.get("city", {})
        if not city:
            stats["no_city"] += 1
        else:
            ct = city.get("title", "?")
            stats["cities"][ct] += 1
            if target_city not in ct.lower():
                stats["wrong_city"] += 1

        ls = p.get("last_seen", {})
        if ls and ls.get("time", 0) > 0 and (time.time() - ls.get("time", 0)) > 180 * 86400:
            stats["inactive"] += 1

        if sc >= 4:
            stats["suspect_count"] += 1
        if sc >= 5:
            stats["bot_count"] += 1
        if sc <= 1:
            stats["clean_count"] += 1

    stats["voters"].sort(key=lambda x: -x["score"])
    answer_stats[aid] = stats

# Corrected votes
corrected = []
for a in answers:
    aid = str(a["id"])
    s = answer_stats[aid]
    clean = s["total"] - s["bot_count"]
    corrected.append({
        "name": s["name"],
        "original": s["total"],
        "bots": s["bot_count"],
        "suspect": s["suspect_count"],
        "corrected": clean,
    })

corrected_total = sum(c["corrected"] for c in corrected)
for c in corrected:
    c["corrected_rate"] = round(c["corrected"] * 100 / corrected_total, 2) if corrected_total else 0
corrected.sort(key=lambda x: -x["corrected"])
original_sorted = sorted(corrected, key=lambda x: -x["original"])

# ============================================================
# HTML helpers
# ============================================================
def pct(n, total):
    return f"{n * 100 / total:.1f}" if total else "0"

def bar(value, max_val, color="#4CAF50"):
    w = value * 100 / max_val if max_val else 0
    return f'<div style="background:{color};height:22px;width:{w:.1f}%;border-radius:3px;min-width:2px"></div>'

def risk_color(sp):
    if sp >= 30: return "#e74c3c"
    if sp >= 20: return "#e67e22"
    if sp >= 15: return "#f39c12"
    return "#27ae60"

def risk_label(sp):
    if sp >= 30: return "ВЫСОКИЙ"
    if sp >= 20: return "ПОВЫШЕННЫЙ"
    if sp >= 15: return "УМЕРЕННЫЙ"
    return "НИЗКИЙ"

target_city_cap = target_city.capitalize()

# ============================================================
# Build HTML
# ============================================================
html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Аудит голосования VK — {poll["question"]}</title>
<style>
:root {{
  --bg: #0f1117; --card: #1a1d27; --card2: #232734;
  --text: #e4e6eb; --text2: #9ca3b0; --accent: #5b8dee;
  --red: #e74c3c; --orange: #e67e22; --yellow: #f39c12; --green: #27ae60;
  --border: #2d3140;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ font-size: 1.8em; margin-bottom: 5px; }}
h2 {{ font-size: 1.4em; margin: 30px 0 15px; color: var(--accent); }}
h3 {{ font-size: 1.1em; margin: 20px 0 10px; }}
.subtitle {{ color: var(--text2); margin-bottom: 25px; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }}
.stat-box {{ background: var(--card2); border-radius: 8px; padding: 15px; text-align: center; }}
.stat-box .num {{ font-size: 2em; font-weight: bold; }}
.stat-box .label {{ color: var(--text2); font-size: 0.85em; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ background: var(--card2); font-weight: 600; color: var(--text2); font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.5px; }}
tr:hover {{ background: var(--card2); }}
.bar-cell {{ width: 200px; }}
.bar-bg {{ background: var(--card2); border-radius: 4px; overflow: hidden; height: 22px; }}
.risk-badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.8em; font-weight: bold; color: white; }}
.change-up {{ color: var(--green); }} .change-down {{ color: var(--red); }} .change-same {{ color: var(--text2); }}
.suspect-table td {{ font-size: 0.9em; }}
.score-pill {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; font-weight: bold; }}
.score-high {{ background: var(--red); color: white; }}
.score-med {{ background: var(--orange); color: white; }}
.score-low {{ background: var(--yellow); color: #222; }}
.score-ok {{ background: var(--green); color: white; }}
.methodology {{ color: var(--text2); font-size: 0.9em; }}
.methodology li {{ margin: 5px 0; }}
.tab-buttons {{ display: flex; gap: 5px; margin-bottom: 15px; flex-wrap: wrap; }}
.tab-btn {{ background: var(--card2); border: 1px solid var(--border); color: var(--text); padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 0.9em; transition: all 0.2s; }}
.tab-btn:hover {{ border-color: var(--accent); }}
.tab-btn.active {{ background: var(--accent); border-color: var(--accent); color: white; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.summary-verdict {{ font-size: 1.1em; padding: 15px; border-radius: 8px; margin: 15px 0; }}
.footer {{ color: var(--text2); font-size: 0.8em; text-align: center; margin-top: 40px; padding: 20px; }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
@media (max-width: 768px) {{ .bar-cell {{ width: 120px; }} }}
</style>
</head>
<body>
<div class="container">

<h1>Аудит голосования VK</h1>
<p class="subtitle">
  &laquo;{poll["question"]}&raquo; &mdash;
  <a href="{post_url}" target="_blank">{post_url.replace("https://","")}</a><br>
  Участников группы: {group_members_count}
  &nbsp;|&nbsp; Дата опроса: {poll_created.strftime("%d.%m.%Y %H:%M")} UTC
  &nbsp;|&nbsp; Дата аудита: {data["timestamp"][:10]}
  &nbsp;|&nbsp; Тип: открытое голосование
</p>

<div class="stats-grid">
  <div class="stat-box"><div class="num">{total_votes}</div><div class="label">Всего голосов</div></div>
  <div class="stat-box"><div class="num">{group_members_count}</div><div class="label">Участников группы</div></div>
  <div class="stat-box"><div class="num" style="color:var(--red)">{sum(s["bot_count"] for s in answer_stats.values())}</div><div class="label">Ботов (score&ge;5)</div></div>
  <div class="stat-box"><div class="num" style="color:var(--orange)">{sum(s["suspect_count"] for s in answer_stats.values())}</div><div class="label">Подозрительных (&ge;4)</div></div>
  <div class="stat-box"><div class="num" style="color:var(--red)">{sum(s["deactivated"] for s in answer_stats.values())}</div><div class="label">Деактивированных</div></div>
  <div class="stat-box"><div class="num" style="color:var(--green)">{corrected_total}</div><div class="label">Верных голосов</div></div>
</div>

<!-- Section 1: Original vs Corrected -->
<h2>1. Оригинальные результаты vs Скорректированные</h2>
<div class="card">
<table>
<tr>
  <th>Вариант</th><th>Голосов</th><th>%</th><th class="bar-cell">Оригинал</th>
  <th>Боты</th><th>Подозр.</th><th style="color:var(--green)">Верных</th>
  <th>Верный %</th><th class="bar-cell">Скорректировано</th><th>Изменение</th>
</tr>
'''

max_orig = max(a["votes"] for a in answers)
max_corr = max(c["corrected"] for c in corrected)

for a in answers:
    aid = str(a["id"])
    s = answer_stats[aid]
    c = [x for x in corrected if x["name"] == s["name"]][0]
    diff = c["corrected_rate"] - a["rate"]
    if diff > 0.5:
        ccls, arrow = "change-up", "&#9650;"
    elif diff < -0.5:
        ccls, arrow = "change-down", "&#9660;"
    else:
        ccls, arrow = "change-same", "&mdash;"

    html += f'''<tr>
  <td>{s["name"]}</td><td><b>{s["total"]}</b></td><td>{a["rate"]}%</td>
  <td class="bar-cell"><div class="bar-bg">{bar(s["total"], max_orig, "#5b8dee")}</div></td>
  <td style="color:var(--red)">{s["bot_count"]}</td><td style="color:var(--orange)">{s["suspect_count"]}</td>
  <td style="color:var(--green)"><b>{c["corrected"]}</b></td><td><b>{c["corrected_rate"]}%</b></td>
  <td class="bar-cell"><div class="bar-bg">{bar(c["corrected"], max_corr, "#27ae60")}</div></td>
  <td class="{ccls}"><b>{arrow} {abs(diff):.1f}%</b></td>
</tr>
'''

html += '</table></div>'

# Section 2: Risk levels
html += '''
<h2>2. Уровень риска накрутки по вариантам</h2>
<div class="card">
<table>
<tr><th>Вариант</th><th>Деактивир.</th><th>Без фото</th><th>Нет города</th>
<th>Не из ''' + target_city_cap + '''</th><th>Закрытые</th><th>Не в группе</th><th>Неактивн.</th><th>Подозрит.%</th><th>Риск</th></tr>
'''

for a in answers:
    aid = str(a["id"])
    s = answer_stats[aid]
    n = s["total"]
    if n == 0:
        continue
    sp = s["suspect_count"] * 100 / n
    html += f'''<tr>
  <td>{s["name"]}</td>
  <td>{s["deactivated"]} ({pct(s["deactivated"],n)}%)</td>
  <td>{s["no_photo"]} ({pct(s["no_photo"],n)}%)</td>
  <td>{s["no_city"]} ({pct(s["no_city"],n)}%)</td>
  <td>{s["wrong_city"]} ({pct(s["wrong_city"],n)}%)</td>
  <td>{s["closed"]} ({pct(s["closed"],n)}%)</td>
  <td>{s["not_member"]} ({pct(s["not_member"],n)}%)</td>
  <td>{s["inactive"]} ({pct(s["inactive"],n)}%)</td>
  <td><b>{sp:.1f}%</b></td>
  <td><span class="risk-badge" style="background:{risk_color(sp)}">{risk_label(sp)}</span></td>
</tr>
'''

html += '</table></div>'

# Section 3: Geography
html += '<h2>3. География голосования</h2><div class="card">'

for a in answers:
    aid = str(a["id"])
    s = answer_stats[aid]
    if not s["cities"]:
        continue
    n = s["total"]
    tc = sum(v for k, v in s["cities"].items() if target_city in k.lower() and "хутор" not in k.lower())
    html += f'<h3>{s["name"]} ({n} голосов)</h3>'
    html += f'<p style="color:var(--text2)">{target_city_cap}: <b>{tc}</b> ({pct(tc,n)}%) | Город не указан: <b>{s["no_city"]}</b> ({pct(s["no_city"],n)}%)</p>'
    html += '<table><tr><th>Город</th><th>Голосов</th><th>%</th></tr>'
    for cname, cnt in s["cities"].most_common(15):
        html += f'<tr><td>{cname}</td><td>{cnt}</td><td>{pct(cnt,n)}%</td></tr>'
    html += '</table><br>'

html += '</div>'

# Section 4: Suspect accounts (tabs)
html += '''<h2>4. Подозрительные аккаунты (score &ge; 4)</h2><div class="card"><div class="tab-buttons">'''

for i, a in enumerate(answers):
    aid = str(a["id"])
    s = answer_stats[aid]
    active = " active" if i == 0 else ""
    short = s["name"].split(". ", 1)[-1][:25]
    html += f'<button class="tab-btn{active}" onclick="showTab(\'{aid}\')">{short} ({s["suspect_count"]})</button>'

html += '</div>'

for i, a in enumerate(answers):
    aid = str(a["id"])
    s = answer_stats[aid]
    active = " active" if i == 0 else ""
    suspects = [v for v in s["voters"] if v["score"] >= 4]

    html += f'<div class="tab-content{active}" id="tab-{aid}">'
    html += f'<p style="color:var(--text2)">Подозрительных: <b>{len(suspects)}</b> из {s["total"]}</p>'
    html += '<table class="suspect-table"><tr><th>#</th><th>Профиль</th><th>Score</th><th>Причины</th></tr>'

    for j, v in enumerate(suspects):
        sc = v["score"]
        pcls = "score-high" if sc >= 5 else "score-med"
        html += f'''<tr>
  <td>{j+1}</td>
  <td><a href="https://vk.com/id{v["id"]}" target="_blank">{v["name"]}</a></td>
  <td><span class="score-pill {pcls}">{sc}</span></td>
  <td>{", ".join(v["reasons"])}</td>
</tr>'''

    html += '</table></div>'

html += '</div>'

# Section 5: Deactivated
html += '''<h2>5. Деактивированные / забаненные аккаунты</h2><div class="card">
<p style="color:var(--text2)">Аккаунты, заблокированные или удалённые ВКонтакте. Голоса с таких аккаунтов — прямое доказательство ботоферм.</p>
<table><tr><th>Вариант</th><th>Деактивир.</th><th>% от голосов</th><th></th></tr>'''

max_deact = max(s["deactivated"] for s in answer_stats.values()) or 1
for a in answers:
    aid = str(a["id"])
    s = answer_stats[aid]
    n = s["total"]
    d = s["deactivated"]
    w = d * 100 / n if n else 0
    color = "#e74c3c" if d > 10 else "#f39c12" if d > 0 else "#27ae60"
    html += f'<tr><td>{s["name"]}</td><td><b style="color:{color}">{d}</b></td><td>{w:.1f}%</td>'
    html += f'<td class="bar-cell"><div class="bar-bg">{bar(d, max_deact, color)}</div></td></tr>'

html += '</table><h3>Полный список деактивированных аккаунтов</h3>'
html += '<table><tr><th>Профиль</th><th>Голосовал за</th></tr>'
for a in answers:
    aid = str(a["id"])
    s = answer_stats[aid]
    for v in s["voters"]:
        if v["deactivated"]:
            html += f'<tr><td><a href="https://vk.com/id{v["id"]}" target="_blank">{v["name"]}</a> (id{v["id"]})</td><td>{s["name"]}</td></tr>'
html += '</table></div>'

# Section 6: Verdict
worst = max(answer_stats.values(), key=lambda s: s["deactivated"])
wp = worst["deactivated"] * 100 / worst["total"] if worst["total"] else 0

html += f'''
<h2>6. Итоговый вердикт</h2>
<div class="card">
<div class="summary-verdict" style="background:rgba(231,76,60,0.15); border-left: 4px solid var(--red);">
  <b>Вариант &laquo;{worst["name"]}&raquo;</b> имеет критические признаки накрутки:
  <ul style="margin:10px 0 0 20px">
    <li><b>{worst["deactivated"]}</b> деактивированных аккаунтов ({wp:.1f}%) — прямое доказательство ботоферм</li>
    <li><b>{worst["suspect_count"]}</b> подозрительных аккаунтов ({worst["suspect_count"]*100/worst["total"]:.1f}%)</li>
    <li><b>{worst["no_photo"]}</b> без аватарки ({worst["no_photo"]*100/worst["total"]:.1f}%)</li>
    <li><b>{worst["bot_count"]}</b> с bot-score &ge;5</li>
  </ul>
</div>
<h3>Скорректированный рейтинг (без ботов score&ge;5)</h3>
<table><tr><th>Место</th><th>Вариант</th><th>Верных голосов</th><th>Верный %</th><th>Было</th><th>Было %</th></tr>
'''

for i, c in enumerate(corrected):
    orig_rank = [x["name"] for x in original_sorted].index(c["name"]) + 1
    rc = orig_rank - (i + 1)
    badge = f'<span class="change-up">&uarr;{rc}</span>' if rc > 0 else (f'<span class="change-down">&darr;{abs(rc)}</span>' if rc < 0 else "")
    orig_rate = [a["rate"] for a in answers if a["text"] == c["name"]][0]
    html += f'''<tr><td><b>{i+1}</b> {badge}</td><td><b>{c["name"]}</b></td>
  <td style="color:var(--green)"><b>{c["corrected"]}</b></td><td><b>{c["corrected_rate"]}%</b></td>
  <td style="color:var(--text2)">{c["original"]}</td><td style="color:var(--text2)">{orig_rate}%</td></tr>'''

html += '</table></div>'

# Section 7: Methodology
html += f'''
<h2>7. Методология</h2>
<div class="card methodology">
<p>Каждому аккаунту присваивается <b>suspicion score</b>:</p>
<ul>
  <li><b>+3</b> — деактивирован/забанен ВКонтакте</li>
  <li><b>+2</b> — неактивен более 180 дней</li>
  <li><b>+1</b> — нет аватарки</li>
  <li><b>+1</b> — не состоит в группе</li>
  <li><b>+1</b> — город не указан</li>
  <li><b>+1</b> — город не {target_city_cap} (локальное голосование)</li>
  <li><b>+1</b> — закрытый профиль + нет аватарки</li>
  <li><b>+1</b> — нет данных об активности</li>
</ul>
<br>
<p><b>Классификация:</b></p>
<ul>
  <li><span class="score-pill score-high">5+</span> — бот (исключается из подсчёта)</li>
  <li><span class="score-pill score-med">4</span> — подозрительный</li>
  <li><span class="score-pill score-low">2-3</span> — слабые признаки</li>
  <li><span class="score-pill score-ok">0-1</span> — чистый</li>
</ul>
<br>
<p>Источник: VK API (wall.getById, polls.getVoters, users.get, groups.getMembers).<br>
Проанализировано <b>{total_votes}</b> аккаунтов.</p>
</div>

<div class="footer">
  Аудит проведён {datetime.now().strftime("%d.%m.%Y %H:%M")} &mdash;
  <a href="{post_url}">{post_url.replace("https://","")}</a>
</div>
</div>

<script>
function showTab(aid) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + aid).classList.add('active');
  event.target.classList.add('active');
}}
</script>
</body>
</html>'''

out_path = "output/report.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Report: {out_path}")
print(f"Votes: {total_votes} | Bots: {sum(s['bot_count'] for s in answer_stats.values())} | Corrected: {corrected_total}")
print()
for i, c in enumerate(corrected):
    print(f"  {i+1}. {c['name']}: {c['corrected']} ({c['corrected_rate']}%) [was {c['original']}, bots={c['bots']}]")
