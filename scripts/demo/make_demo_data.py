#!/usr/bin/env python3
"""Generate a fully fabricated demo archive for README screenshots.

No real personal data — every name, topic, and utterance is invented.
"""
import json
import subprocess
import sys
from pathlib import Path

DEMO = Path(sys.argv[1]).resolve()


def note_md(title_line, iso, orig, summary_en, summary_zh, kp_en, kp_zh, actions, transcript):
    kp_en_s = "\n".join(f"- {k}" for k in kp_en)
    kp_zh_s = "\n".join(f"- {k}" for k in kp_zh)
    act_s = "\n".join(f"- [ ] {a}" for a in actions) if actions else "- (none)"
    return f"""# {title_line}

**Recorded:** {iso}
**Source:** Apple Watch Voice Memo
**File:** `{orig}`

## Summary

{summary_en}

{summary_zh}

## Key Points

{kp_en_s}

{kp_zh_s}

## Action Items

{act_s}

---

## Transcript

```
{transcript}
```
"""


def t(lines):
    return "\n".join(f"[{a} - {b}] {who}: {txt}" for a, b, who, txt in lines)


REC_A_TRANSCRIPT = t([
    ("00:00:00", "00:00:09", "阿星", "我昨晚想了一下咖啡机器人这个 idea，核心不是做硬件，是做排队体验。"),
    ("00:00:10", "00:00:18", "Mia", "对，写字楼早高峰那十五分钟，大家要的其实是确定性——几点几分能拿到。"),
    ("00:00:19", "00:00:31", "阿星", "所以入口应该是日历集成，会议开始前十分钟自动下单，走到楼下正好取。"),
    ("00:00:32", "00:00:40", "Mia", "这个 hook 好。那 MVP 先不做机器人，先做一个虚拟排队 + 到点提醒的小程序？"),
    ("00:00:41", "00:00:55", "阿星", "嗯，先验证需求。硬件那边我问过老同学，一台改装机的成本大概八万，回本周期太长了。"),
    ("00:00:56", "00:01:07", "Mia", "那定价怎么想？订阅制的话，一天一杯封顶，月卡三百九？"),
    ("00:01:08", "00:01:20", "阿星", "可以 A/B 一下。另外 retention 的关键可能是口味记忆——上次的奶量、温度直接带出来。"),
    ("00:01:21", "00:01:33", "Mia", "这个数据结构要想清楚，user profile 里存 preference vector，别到时候重构。"),
    ("00:01:34", "00:01:48", "阿星", "对了，名字我想叫「灯塔咖啡」，寓意是早上那一杯把人点亮，你觉得呢？"),
    ("00:01:49", "00:01:58", "Mia", "比「快取咖啡」有记忆点。域名和商标这周查一下,别又被抢注。"),
    ("00:01:59", "00:02:14", "阿星", "行,那分工:我出产品原型和排队算法的 demo,你去聊三家写字楼的物业。"),
    ("00:02:15", "00:02:26", "Mia", "OK,下周三之前给你物业的反馈。对了 pitch deck 用你上次那套模板就行。"),
    ("00:02:27", "00:02:41", "阿星", "好。还有一个点,机器人取餐口的高度要考虑轮椅用户,这个从第一版就得放进设计约束。"),
    ("00:02:42", "00:02:50", "Mia", "同意,无障碍不是加分项,是底线。今天就先到这?"),
    ("00:02:51", "00:02:58", "阿星", "嗯,散会。我把这段录音丢给 AI 整理一下发你。"),
])

REC_B_TRANSCRIPT = t([
    ("00:00:00", "00:00:12", "SPEAKER_1", "刚合上《纳瓦尔宝典》,有个点想趁热记下来——他说财富是睡后收入,地位是零和游戏。"),
    ("00:00:13", "00:00:27", "SPEAKER_1", "我以前混淆了这两件事,把「在圈子里有名」当成了目标,其实那是地位,不是杠杆。"),
    ("00:00:28", "00:00:44", "SPEAKER_1", "真正的杠杆是代码和内容,写一次跑无数次。这个跟我做小工具的思路是一致的,but I keep forgetting it。"),
    ("00:00:45", "00:01:01", "SPEAKER_1", "行动上:下个月把重复回答的问题都沉淀成文章,一劳永逸。这才是 compounding。"),
    ("00:01:02", "00:01:15", "SPEAKER_1", "还有一句很扎——「你和你不能舍弃的东西之间,隔着你想要的一切」。今晚就把囤了半年没看的收藏夹清了。"),
])

REC_C_TRANSCRIPT = t([
    ("00:00:00", "00:00:10", "老周", "呼伦贝尔那条线我查了,海拉尔进,满洲里出,五天四晚比较从容。"),
    ("00:00:11", "00:00:22", "阿星", "第一天直接扎到额尔古纳?还是先在海拉尔缓一晚倒时差?"),
    ("00:00:23", "00:00:35", "Mia", "缓一晚吧,落地都下午了。第二天一早走莫尔道嘎,白桦林那段慢慢开。"),
    ("00:00:36", "00:00:50", "老周", "对,那段是精华,别赶路。室韦住两晚,可以骑马和看日落,民宿我收藏了两家。"),
    ("00:00:51", "00:01:03", "阿星", "租车我来搞定,SUV 底盘高一点稳妥。老周你把民宿链接发群里,今晚就定。"),
    ("00:01:04", "00:01:15", "Mia", "预算我拉个表,机票 + 租车 + 住宿人均控制在四千五以内。"),
    ("00:01:16", "00:01:28", "老周", "最后一天满洲里看国门和套娃广场,晚上的航班回,行程闭环。"),
    ("00:01:29", "00:01:36", "阿星", "完美,就这么定。谁也不许临时加班放鸽子啊。"),
])

RECS = [
    {
        "key": "2026-07-12 093015",
        "date": "2026-07-12", "hhmmss": "093015",
        "stem": "093015-咖啡机器人产品头脑风暴",
        "title": "2026-07-12 09:30 咖啡机器人产品头脑风暴",
        "original": "20260712 093015-1A2B3C4D.m4a",
        "category": "工作商务",
        "speakers": {"SPEAKER_1": "阿星", "SPEAKER_2": "Mia"},
        "attach": ("GPT-分析.md", """# 头脑风暴复盘 · AI 分析

## 一句话结论

这次讨论的最大共识:**先用虚拟排队小程序验证「确定性取餐」需求,硬件后置。**

## 决策清单

| 决策 | 负责人 | 截止 |
|---|---|---|
| 产品原型 + 排队算法 demo | 阿星 | 下周三 |
| 三家写字楼物业访谈 | Mia | 下周三 |
| 「灯塔咖啡」商标与域名检索 | Mia | 本周 |

## 风险提示

> 硬件单台成本约 ¥80,000,回本周期长 —— 在需求验证通过前**不要**启动采购。

## 值得展开的点

- 口味记忆(preference vector)是 retention 的关键假设,值得单独设计实验
- 无障碍取餐口从 v1 进设计约束,这是团队价值观的体现
"""),
        "summary_en": "Two co-founders brainstorm a coffee-robot startup and converge on validating demand first with a virtual-queue mini-app before touching hardware. They sketch the calendar-triggered ordering hook, debate subscription pricing, name the project, and split next-week tasks.",
        "summary_zh": "两位创始人围绕咖啡机器人项目展开头脑风暴,达成共识:先用虚拟排队小程序验证「确定性取餐」需求,再考虑硬件。讨论了日历触发下单的产品钩子、订阅定价、品牌命名「灯塔咖啡」,并明确了下周分工。",
        "kp_en": [
            "MVP is a virtual queue + pickup-time notification mini-app — hardware deferred until demand is validated.",
            "Calendar integration auto-orders ten minutes before a meeting so the coffee is ready on arrival.",
            "Taste memory (milk, temperature) stored as a preference vector is the key retention hypothesis.",
            "Accessibility (wheelchair-height pickup) enters the design constraints from v1.",
        ],
        "kp_zh": [
            "MVP 是虚拟排队 + 到点提醒小程序,硬件在需求验证前不启动。",
            "日历集成会前十分钟自动下单,到楼下正好取餐。",
            "口味记忆(奶量、温度)存成 preference vector,是留存的关键假设。",
            "无障碍取餐口高度从第一版进设计约束。",
        ],
        "actions": [
            "阿星: 产出产品原型与排队算法 demo。 / Ash: build the product prototype and queue-algorithm demo.",
            "Mia: 完成三家写字楼物业访谈并反馈。 / Mia: interview three office-building property managers.",
        ],
        "transcript": REC_A_TRANSCRIPT,
    },
    {
        "key": "2026-07-12 214500",
        "date": "2026-07-12", "hhmmss": "214500",
        "stem": "214500-纳瓦尔宝典读后随想",
        "title": "2026-07-12 21:45 《纳瓦尔宝典》读后随想",
        "original": "20260712 214500-5E6F7A8B.m4a",
        "category": "学习认知",
        "speakers": {},
        "attach": None,
        "summary_en": "A solo reflection after finishing The Almanack of Naval Ravikant: separating wealth (leverage, permissionless) from status (zero-sum), recommitting to write-once-run-forever assets, and a resolution to clear the hoarded reading list tonight.",
        "summary_zh": "读完《纳瓦尔宝典》后的独白随想:区分财富(杠杆)与地位(零和),重申「写一次、跑无数次」的复利思路,并决定今晚清空囤积半年的收藏夹。",
        "kp_en": [
            "Wealth is leverage; status is a zero-sum game — the two were previously conflated.",
            "Code and content are permissionless leverage: write once, run forever.",
            "Plan: turn repeatedly-answered questions into articles next month.",
        ],
        "kp_zh": [
            "财富是杠杆,地位是零和游戏,过去把两者混为一谈。",
            "代码和内容是无需许可的杠杆:写一次,跑无数次。",
            "下个月把重复回答的问题沉淀成文章。",
        ],
        "actions": ["把收藏夹里囤积的文章清零。 / Clear the hoarded read-later list tonight."],
        "transcript": REC_B_TRANSCRIPT,
    },
    {
        "key": "2026-07-13 183020",
        "date": "2026-07-13", "hhmmss": "183020",
        "stem": "183020-呼伦贝尔自驾路线计划",
        "title": "2026-07-13 18:30 呼伦贝尔自驾路线计划",
        "original": "20260713 183020-9C0D1E2F.m4a",
        "category": "生活日常",
        "speakers": {"SPEAKER_1": "老周", "SPEAKER_2": "阿星", "SPEAKER_3": "Mia"},
        "attach": None,
        "summary_en": "Three friends lock in a five-day Hulunbuir road trip: Hailar in, Manzhouli out, two nights in Shiwei, with car rental, homestays, and a ¥4,500-per-person budget split among them.",
        "summary_zh": "三位朋友敲定呼伦贝尔五天四晚自驾:海拉尔进、满洲里出,室韦住两晚,分工搞定租车、民宿与人均四千五的预算表。",
        "kp_en": [
            "Route: Hailar → Ergun → Mordaga birch forest → two nights in Shiwei → Manzhouli.",
            "Task split: car rental (阿星), homestay booking (老周), budget sheet capped at ¥4,500/person (Mia).",
        ],
        "kp_zh": [
            "路线:海拉尔 → 额尔古纳 → 莫尔道嘎白桦林 → 室韦两晚 → 满洲里。",
            "分工:租车(阿星)、民宿(老周)、人均预算 ¥4,500 封顶(Mia)。",
        ],
        "actions": ["老周: 今晚把两家民宿链接发群里并下定。 / Zhou: share and book the two homestays tonight."],
        "transcript": REC_C_TRANSCRIPT,
    },
]


def main():
    DEMO.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for r in RECS:
        day = DEMO / r["date"]
        day.mkdir(exist_ok=True)
        note_rel = f"{r['date']}/{r['stem']}.md"
        audio_rel = f"{r['date']}/{r['stem']}.m4a"
        iso = f"{r['date']}T{r['hhmmss'][:2]}:{r['hhmmss'][2:4]}:{r['hhmmss'][4:]}"
        (DEMO / note_rel).write_text(
            note_md(r["title"], iso, r["original"], r["summary_en"], r["summary_zh"],
                    r["kp_en"], r["kp_zh"], r["actions"], r["transcript"]),
            encoding="utf-8",
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             "anullsrc=r=44100:cl=mono", "-t", "2", "-c:a", "aac", str(DEMO / audio_rel)],
            check=True,
        )
        entry = {
            "original": r["original"],
            "title": r["title"],
            "category": r["category"],
            "note": note_rel,
            "audio": audio_rel,
        }
        if r["speakers"]:
            entry["speakers"] = r["speakers"]
            entry["speakers_applied"] = r["speakers"]
        if r["attach"]:
            adir = day / f"{r['hhmmss']}-attachments"
            adir.mkdir(exist_ok=True)
            name, body = r["attach"]
            (adir / name).write_text(body, encoding="utf-8")
            entry["attachments"] = [f"{r['date']}/{r['hhmmss']}-attachments/{name}"]
        manifest[r["key"]] = entry

    (DEMO / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (DEMO / "speakers.json").write_text(
        json.dumps({"阿星": "#6cb0f5", "Mia": "#e08fb7", "老周": "#8fbf6a"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"demo archive at {DEMO}: {len(manifest)} recordings")


if __name__ == "__main__":
    main()
