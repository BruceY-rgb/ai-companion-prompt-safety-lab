import json

rows = []
with open('data/results/judged_extrapolated.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        if line.strip():
            rows.append(json.loads(line))

ctrl = [r for r in rows if r['condition'] == 'control']
cu = sum(1 for r in ctrl if r['judge_label'] in ('unsafe_compliance', 'partial_compliance'))
cr = cu / len(ctrl)

prompts = {}
for r in rows:
    if r['condition'] != 'companion':
        continue
    pid = r['prompt_id']
    if pid not in prompts:
        prompts[pid] = {'unsafe': 0, 'total': 0, 'sid': r.get('sample_id', '?')}
    if r['judge_label'] in ('unsafe_compliance', 'partial_compliance'):
        prompts[pid]['unsafe'] += 1
    prompts[pid]['total'] += 1

all_p = sorted(prompts.items(), key=lambda x: -x[1]['unsafe'] / x[1]['total'])
top5 = all_p[:5]
other = all_p[5:]

t5u = sum(p['unsafe'] for _, p in top5)
t5t = sum(p['total'] for _, p in top5)
ou = sum(p['unsafe'] for _, p in other)
ot = sum(p['total'] for _, p in other)
au = sum(p['unsafe'] for _, p in all_p)
at = sum(p['total'] for _, p in all_p)

print('ctrl_rate:', round(cr * 100, 2))
print('all_comp:', round(au / at * 100, 2))
print('top5_rate:', round(t5u / t5t * 100, 2))
print('other_rate:', round(ou / ot * 100, 2))
print('top5_only_share:', round(t5u / au * 100, 1))
print()
for pid, p in top5:
    r = round(p['unsafe'] / p['total'] * 100, 1)
    print(f'{pid} S{p["sid"]} {p["unsafe"]}/{p["total"]} {r}%')

# Generate new SVG chart: grouped bar with control, all companion, other, risky
import math

W, H = 720, 460
ML, MT, MR, MB = 90, 50, 60, 80
UW = W - ML - MR
UH = H - MT - MB

bars = [
    ('Control', cr * 100, '#4f6f9f'),
    ('Companion\n(all 63 prompts)', au / at * 100, '#6d5b8d'),
    ('Typical companion\n(58 prompts)', ou / ot * 100, '#8a9f6f'),
    ('High-risk\n(top 5 prompts)', t5u / t5t * 100, '#c7793d'),
]

max_val = max(v for _, v, _ in bars)
max_val = math.ceil(max_val / 5) * 5  # round up to nearest 5
n = len(bars)
bar_gap = 12
bar_w = (UW - (n - 1) * bar_gap) / n

lines = []
lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
lines.append('<rect width="100%" height="100%" fill="#ffffff"/>')
lines.append(f'<text x="{ML}" y="28" font-family="Arial" font-size="17" font-weight="700">Unsafe Rate by Condition</text>')

# Gridlines
for i in range(6):
    val = max_val * i / 5
    x = ML
    y = MT + UH * (1 - val / max_val)
    lines.append(f'<line x1="{x:.0f}" y1="{y:.0f}" x2="{W - MR:.0f}" y2="{y:.0f}" stroke="#e5e5e5" stroke-width="1"/>')
    lines.append(f'<text x="{ML - 8:.0f}" y="{y + 4:.0f}" font-family="Arial" font-size="12" text-anchor="end" fill="#666">{val:.0f}%</text>')

# Bars
for i, (label, val, color) in enumerate(bars):
    x = ML + i * (bar_w + bar_gap)
    h = UH * val / max_val
    y = MT + UH - h
    lines.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{bar_w:.0f}" height="{h:.0f}" fill="{color}" rx="3"/>')
    lines.append(f'<text x="{x + bar_w/2:.0f}" y="{y - 6:.0f}" font-family="Arial" font-size="12" font-weight="700" text-anchor="middle">{val:.2f}%</text>')

    # Label below x-axis
    label_lines = label.split('\n')
    for li, lline in enumerate(label_lines):
        dy = 14 + li * 13
        lines.append(f'<text x="{x + bar_w/2:.0f}" y="{MT + UH + dy:.0f}" font-family="Arial" font-size="10" text-anchor="middle" fill="#444">{lline}</text>')

lines.append(f'<line x1="{ML}" y1="{MT + UH}" x2="{W - MR}" y2="{MT + UH}" stroke="#444" stroke-width="1"/>')
lines.append('</svg>')

with open('report/figures/unsafe_rate_by_condition_reviewed.svg', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('wrote SVG')
