"""几何校验（SIFT + RANSAC 二次排序）的测试。

分两层：

* **机制层** —— 真实 :class:`Verifier`：同一张画必须拿到高内点，不同张画必须低；
* **判定层** —— 用桩校验器精确控制内点数，验证 ``recognize_crop`` 的取舍分支。
  真实 SIFT 的内点是连续量，很难恰好构造出"中间地带"，所以判定逻辑用桩来测。
"""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw

from bestdori_helper.config import Settings
from bestdori_helper.models import Card, Catalog
from bestdori_helper.vision.index import Match
from bestdori_helper.vision.recognize import (
    CANDIDATE_LIMIT,
    TOP_K,
    recognize_crop,
)
from bestdori_helper.vision.verify import (
    CONFIRM_INLIERS,
    DEFAULT_TOP_K,
    Verifier,
    build_verifier,
)

from conftest import synth_art

ART_SIZE = (180, 180)


def _textured(seed: int, size: tuple[int, int] = ART_SIZE) -> Image.Image:
    """在 :func:`synth_art` 基础上加大量细碎结构。

    ``synth_art`` 是"平滑渐变 + 大色块"，几乎没角点 —— SIFT 在上面只找得到
    十几个关键点，测不动几何校验。真实卡面有大量纹理和描边，所以这里补上
    小方块阵列，模拟真实卡面的关键点密度。
    """
    img = synth_art(seed, size=size).copy()
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    w, h = size
    for _ in range(70):
        x, y = rng.randrange(0, w - 10), rng.randrange(0, h - 10)
        d.rectangle([x, y, x + rng.randrange(3, 10), y + rng.randrange(3, 10)],
                    fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    return img


def _as_game_crop(seed: int) -> Image.Image:
    """模拟游戏格：缩到 143px 再过一遍 JPEG。"""
    buf = io.BytesIO()
    _textured(seed).resize((143, 143), Image.Resampling.LANCZOS).save(
        buf, format="JPEG", quality=82)
    buf.seek(0)
    with Image.open(buf) as im:
        return im.convert("RGB")


def _catalog_with_art(settings: Settings, arts: dict[int, int],
                      *, textured: bool = False) -> Catalog:
    """建一个 catalog，并把 ``{卡号: 种子}`` 的卡面写进卡图缓存。"""
    settings.ensure_dirs()
    maker = _textured if textured else synth_art
    cards = {}
    for cid, seed in arts.items():
        rs = f"res{cid:06d}"
        cards[cid] = Card(id=cid, character_id=1, rarity=4, attribute="cool",
                          prefix=["", "", "", f"卡{cid}"], resource_set_name=rs)
        maker(seed, size=ART_SIZE).save(settings.thumb_dir / f"{rs}_normal.png")
    return Catalog(settings=settings, cards=cards, characters={}, bands={})


# ---------------------------------------------------------------- 开关

def test_build_verifier_honours_setting(tmp_path: Path) -> None:
    s = Settings(home=tmp_path, verify_sift=False)
    assert build_verifier(_catalog_with_art(s, {1: 1})) is None


def test_build_verifier_returns_none_without_opencv(tmp_path: Path, monkeypatch) -> None:
    """没装 opencv 时必须优雅降级，而不是把整个识别流程炸掉。"""
    import builtins

    real_import = builtins.__import__

    def fake(name, *a, **kw):
        if name == "cv2":
            raise ImportError("no cv2")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    s = Settings(home=tmp_path)
    assert build_verifier(_catalog_with_art(s, {1: 1})) is None


# ---------------------------------------------------------------- 机制

def test_same_art_gets_many_more_inliers_than_different_art(tmp_path: Path) -> None:
    """核心性质：同一张画的内点数必须**远超**不同张画。

    这是整个方案的立足点 —— 真实数据上 137/140 格能拿到内点 >= 60 的候选，
    错配候选都 < 20，中间地带一格没有。
    """
    s = Settings(home=tmp_path)
    cat = _catalog_with_art(s, {1: 1, 2: 2}, textured=True)
    v = Verifier()
    query = _as_game_crop(1)

    same, _ = v.rerank(query, [Match(1, False, 0.5, 0.5, 0.5)], cat)
    diff, _ = v.rerank(query, [Match(2, False, 0.9, 0.9, 0.9)], cat)

    assert same[0].inliers >= CONFIRM_INLIERS, f"同图内点太少：{same[0].inliers}"
    assert diff[0].inliers < CONFIRM_INLIERS, f"异图不该被确认：{diff[0].inliers}"
    assert same[0].inliers >= 3 * max(1, diff[0].inliers), (
        f"同图内点 {same[0].inliers} 相对异图 {diff[0].inliers} 没拉开差距")


def test_rerank_puts_the_true_card_first(tmp_path: Path) -> None:
    """全局分把错的排前面时，几何校验必须把它翻过来。"""
    s = Settings(home=tmp_path)
    cat = _catalog_with_art(s, {1: 1, 2: 2}, textured=True)
    v = Verifier()
    ranked, st = v.rerank(_as_game_crop(1), [Match(2, False, 0.9, 0.9, 0.9),
                                             Match(1, False, 0.5, 0.5, 0.5)], cat)
    assert ranked[0].card_id == 1
    assert st.compared == 2


def test_consecutive_queries_are_independent(tmp_path: Path) -> None:
    """连着查多个格子，每格必须用**自己**的特征。

    回归：查询端特征曾经用固定缓存键 ``"q"``，导致第一个格子的特征被后面
    所有格子复用 —— 表现为一大片格子返回同一个候选和同一份内点数，
    真实截图端到端准确率掉到 38%（掉了 60 个百分点）。
    """
    s = Settings(home=tmp_path)
    cat = _catalog_with_art(s, {1: 1, 2: 2}, textured=True)
    v = Verifier()
    cands = [Match(1, False, 0.6, 0.6, 0.6), Match(2, False, 0.6, 0.6, 0.6)]

    a, _ = v.rerank(_as_game_crop(1), list(cands), cat)
    b, _ = v.rerank(_as_game_crop(2), list(cands), cat)

    assert a[0].card_id == 1, "第 1 格该命中卡 1"
    assert b[0].card_id == 2, "第 2 格该命中卡 2 —— 不共用第 1 格的查询特征"
    assert a[0].inliers >= CONFIRM_INLIERS and b[0].inliers >= CONFIRM_INLIERS


def test_rerank_skips_candidates_without_art(tmp_path: Path) -> None:
    """卡图文件不在本地时跳过该候选，而不是抛异常。"""
    s = Settings(home=tmp_path)
    cat = _catalog_with_art(s, {1: 1}, textured=True)
    v = Verifier()
    ranked, st = v.rerank(_textured(1),
                          [Match(99, False, 0.9, 0.9, 0.9), Match(1, False, 0.5, 0.5, 0.5)], cat)
    assert [m.card_id for m in ranked] == [1]
    assert st.compared == 1


def test_match_carries_verify_fields() -> None:
    m = Match(1, False, 0.8, 0.7, 0.9, good=120, inliers=110)
    assert m.to_dict()["inliers"] == 110 and m.to_dict()["good"] == 120
    assert Match(1, False, 0.8, 0.7, 0.9).inliers == 0, "默认必须是 0（未校验）"


# ---------------------------------------------------------------- 判定逻辑

class _StubVerifier:
    """把内点数直接写死，用来精确测判定分支。"""

    def __init__(self, table: dict[int, int]) -> None:
        self.table = table
        self.calls: list[int] = []

    def rerank(self, crop, candidates, catalog):
        from dataclasses import replace

        self.calls.append(len(candidates))
        out = [replace(c, good=0, inliers=self.table.get(c.card_id, 0)) for c in candidates]
        out.sort(key=lambda m: (-m.inliers, -m.score))
        return out, None


class _FakeIndex:
    def __init__(self, matches: list[Match]) -> None:
        self.matches = matches
        self.top_ks: list[int] = []

    def search(self, *_a, **_kw):
        self.top_ks.append(_kw.get("top_k", 0))
        return list(self.matches[: _kw.get("top_k", len(self.matches))])


def _pipe(settings: Settings, arts: dict[int, int], matches: list[Match],
          verifier, query_seed: int = 1):
    cat = _catalog_with_art(settings, arts)
    return recognize_crop(synth_art(query_seed, size=ART_SIZE),  # type: ignore[arg-type]
                          _FakeIndex(matches), cat, verifier=verifier)


def test_geometric_evidence_overrides_score(tmp_path: Path) -> None:
    """全局分低的卡，只要几何上确认是同一张画，就该采信它。

    这正是真实截图上的主要失败模式：同角色同属性的不同卡全局特征分不开
    （错误卡 0.9 / 正确卡 0.5），但只有正确卡能通过几何校验。
    """
    s = Settings(home=tmp_path)
    item = _pipe(s, {1: 1, 2: 2},
                 [Match(2, False, 0.90, 0.90, 0.90), Match(1, False, 0.50, 0.50, 0.50)],
                 _StubVerifier({1: 150, 2: 3}))
    assert item.card_id == 1
    assert item.status == "matched"
    assert "sift" in item.matched_by


def test_mid_band_is_ambiguous_not_matched(tmp_path: Path) -> None:
    """中间地带（有几分像但不够硬）宁可让人点一下，也不自动采信。"""
    s = Settings(home=tmp_path)
    mid = 10 + (CONFIRM_INLIERS - 10) // 2
    item = _pipe(s, {1: 1, 2: 2},
                 [Match(2, False, 0.90, 0.90, 0.90), Match(1, False, 0.50, 0.50, 0.50)],
                 _StubVerifier({1: mid, 2: 1}))
    assert item.status == "ambiguous"
    assert item.card_id == 1


def test_all_candidates_fail_geometry_falls_back(tmp_path: Path) -> None:
    """几何上一个都不像时，退回原来的全局分判定，不要凭校验把结果判死。"""
    s = Settings(home=tmp_path)
    item = _pipe(s, {1: 1, 2: 2},
                 [Match(2, False, 0.90, 0.90, 0.90), Match(1, False, 0.50, 0.50, 0.50)],
                 _StubVerifier({1: 2, 2: 1}))
    assert item.card_id == 2, "退回全局分路径，top-1 仍是全局分最高的那个"
    assert item.status == "matched"


def test_escalates_shortlist_when_not_confirmed(tmp_path: Path) -> None:
    """首轮没确认时要把候选从 top-30 扩到 top-200 再试一次。"""
    s = Settings(home=tmp_path)
    idx = _FakeIndex([Match(i, False, 0.9 - i * 0.01, 0.9, 0.9) for i in range(1, 3)])
    cat = _catalog_with_art(s, {1: 1})
    recognize_crop(synth_art(1, size=ART_SIZE), idx, cat,  # type: ignore[arg-type]
                   verifier=_StubVerifier({}))
    # recognize_crop 先用 TOP_K 拉一遍候选，再走几何校验的粗筛（DEFAULT_TOP_K），
    # 所以断言按"包含"写，不按顺序写。
    assert TOP_K in idx.top_ks
    assert DEFAULT_TOP_K in idx.top_ks, f"必须先按默认候选数粗筛，实得 {idx.top_ks}"
    assert any(k > DEFAULT_TOP_K for k in idx.top_ks), (
        f"未确认时必须扩大候选重试，实得 {idx.top_ks}")


def test_candidates_are_deduped_and_capped(tmp_path: Path) -> None:
    s = Settings(home=tmp_path)
    dupes = [Match(1, False, 0.9, 0.9, 0.9), Match(1, False, 0.8, 0.8, 0.8)]
    dupes += [Match(i, False, 0.7 - i * 0.01, 0.7, 0.7) for i in range(2, 30)]
    item = _pipe(s, {1: 1, 2: 2}, dupes, None)
    assert len(item.candidates) <= CANDIDATE_LIMIT
    keys = [(c.card_id, c.trained) for c in item.candidates]
    assert len(keys) == len(set(keys))


def test_no_verifier_keeps_old_behaviour(tmp_path: Path) -> None:
    """不给校验器时行为必须和改动前一模一样（旧路径不能被破坏）。"""
    s = Settings(home=tmp_path)
    item = _pipe(s, {1: 1, 2: 2},
                 [Match(2, False, 0.90, 0.90, 0.90), Match(1, False, 0.50, 0.50, 0.50)], None)
    assert item.card_id == 2 and item.status == "matched"
    assert all(c.inliers == 0 for c in item.candidates)
