"""把幻灯片 + 口播稿合成剪映草稿（含 TTS 配音、字幕、BGM）。

核心思路：**逐句生成 TTS**，用**实测音频时长**当时间轴，
而不是拿估算值硬套 —— 这样字幕、配音、画面三者天然对齐，不用后期挪。

- 每句：生成 TTS -> ffprobe 测真实时长 -> 按这个时长排下一句
- 每页：页内第一句到最后一句 = 这一页图片的显示区间（含句间停顿）
- 某句 TTS 失败：退回按字数估算，不中断整体流程

产物：剪映草稿「BestdoriHelper 使用说明」（在剪映的草稿目录里）
用法：python scripts/build_video.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

SKILL_ROOT = r"C:\Users\Frankieson\.workbuddy-ai\skills\jianying-editor"
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))

from utils.env_setup import setup_env           # noqa: E402
setup_env()

from jy_wrapper import JyProject                # noqa: E402
from universal_tts import generate_voice_with_meta  # noqa: E402
# 断句和间隔常量都从 make_lrc 复用 —— 只维护一份，LRC 和视频不会两边不一致
from make_lrc import (                          # noqa: E402
    PAGES, GAP_IN_PAGE, GAP_BETWEEN, SECTION_HOLD, MIN_DUR,
)

PROJ = REPO / "BestdoriHelper-使用说明"
RENDER = PROJ / "render"

# 注意：overwrite=True 会先尝试删掉同名旧草稿，但那个删除走的是「回收站」机制，
# 剪映占着目录时会失败（safe-delete fail-closed）。所以改稿子时**换个名字**最省事，
# 旧草稿在剪映里手动删。
DRAFT_NAME = "BestdoriHelper 使用说明 精简版"
SPEAKER = "zh_female_xiaopengyou"   # 温柔女声，讲工具类内容合适

TEMP = Path(os.environ.get("TEMP", ".")) / "bdh_tts"
TEMP.mkdir(parents=True, exist_ok=True)


def probe_duration(path: str) -> float | None:
    """用 ffprobe 拿真实音频时长（秒）。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return None


def est_duration(text: str) -> float:
    """估算时长（TTS 失败时兜底）：中文 1 音节 / 秒，英文 0.55，标点 0.4。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    asc = len(re.findall(r"[A-Za-z0-9]", text))
    pun = len(re.findall(r"[，。、；：？！,.;:?!]", text))
    return max(MIN_DUR, (cjk + asc * 0.55 + pun * 0.4) / 4.3)


def main() -> int:
    imgs = sorted(RENDER.glob("*.png"))
    n_pages = len(PAGES)
    if len(imgs) != n_pages:
        print(f"✗ 需要 {n_pages} 张页面图，实际 {len(imgs)} 张。先跑 scripts/render_slides.py")
        return 1

    project = JyProject(DRAFT_NAME, overwrite=True)

    t = 0.0
    done = 0
    failed = 0
    rows: list[tuple[float, float, str]] = []   # (起点, 时长, 文本) 用来回写 LRC

    for pi, page in enumerate(PAGES, start=1):
        page_start = t
        img = imgs[pi - 1]

        for sent in page:
            ogg = TEMP / f"tts_{uuid.uuid4().hex[:8]}.ogg"
            dur = None
            try:
                res = asyncio_run(sent, str(ogg))
                if res and res[0] and os.path.exists(res[0]):
                    dur = probe_duration(res[0])
                    if dur:
                        project.add_media_safe(res[0], f"{t}s",
                                               track_name="VoiceOver")
            except Exception as e:
                print(f"    ! TTS 失败({e})，改用估算")

            if not dur:
                dur = est_duration(sent)
                failed += 1

            # 字幕：和这句配音同一区间
            project.add_text_simple(sent, start_time=f"{t}s", duration=f"{dur}s",
                                    track_name="Subtitles")
            rows.append((t, dur, sent))
            t += dur + GAP_IN_PAGE
            done += 1

        # 章节扉页（只有一句）额外停留
        if len(page) == 1:
            t += SECTION_HOLD
        t += GAP_BETWEEN - GAP_IN_PAGE

        span = t - page_start
        project.add_media_safe(str(img), f"{page_start}s", duration=f"{span}s",
                               track_name="VideoTrack")
        print(f"  第 {pi:>2} 页  {page_start:6.1f}s → {t:6.1f}s  ({span:.1f}s)", flush=True)

    # BGM（有配音时必须压到 0.6）
    try:
        bgm = project.add_cloud_music("科技", start_time="0s", track_name="BGM_Track")
        if bgm:
            bgm.volume = 0.6
            print("  ✓ BGM 已加（音量 0.6）")
    except Exception as e:
        print(f"  ! BGM 跳过：{e}")

    project.save()
    write_lrc(rows)   # 用 TTS 实测时长回写，保证 LRC 和视频完全一致
    print(f"\n✅ 草稿已生成：{DRAFT_NAME}")
    print(f"   字幕 {done} 句 / {n_pages} 页 / 总时长 {int(t // 60)} 分 {t % 60:.0f} 秒")
    if failed:
        print(f"   ⚠ {failed} 句 TTS 失败，用了估算时长（剪映里可能需要微调）")
    return 0


def write_lrc(rows: list[tuple[float, float, str]]) -> None:
    """用 TTS 实测时长回写 LRC —— 让字幕文件和成片严格对齐。

    make_lrc.py 那版是按字数估的（约 4.3 音节/秒），和真实配音有出入；
    这里拿的是 ffprobe 量出来的真实时长，更准。
    """
    def stamp(sec: float) -> str:
        m, s = divmod(max(0.0, sec), 60)
        return f"[{int(m):02d}:{s:05.2f}]"

    lines = [
        "[ti:BestdoriHelper 使用说明]",
        "[ar:口播稿]",
        "[al:BestdoriHelper 使用说明]",
        "[by:scripts/build_video.py（TTS 实测时长）]",
        "[offset:0]",
        "",
    ]
    for start, _dur, text in rows:
        lines.append(f"{stamp(start)}{text}")
    out = PROJ / "口播稿.lrc"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def asyncio_run(text: str, out: str):
    import asyncio
    return asyncio.run(
        generate_voice_with_meta(text, out, SPEAKER, sami_retries=2))


if __name__ == "__main__":
    raise SystemExit(main())
