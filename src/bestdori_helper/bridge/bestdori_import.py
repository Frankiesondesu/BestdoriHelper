"""把本地清单同步到 Bestdori 的「我的卡牌」(Profile Cards)。

Bestdori 的集成点（已通过分析其前端 bundle 确认）
--------------------------------------------------
Bestdori 前端路由与模块（来自 ``app.js`` 路由表与 ``ProfileCards`` chunk）::

    /profile/manager      Profile Manager —— 创建档案（服务器 / 活动 / 大师等级 / 技能等级）
    /profile/cards        Profile Cards   —— **登记持有卡牌**，就是我们要写入的目标
    /profile/items        Profile Items   —— 区域道具

``ProfileCards`` 模块内部使用的设置键包括 ``profile/cards/displayMode``、
``profile/cards/optionSortBy``，添加卡片的入口是 ``profile/cards/add``。
这些是**登录态下的账号数据**，必须先登录才能读写。

因此这里提供三层导入方案，从稳到快
----------------------------------
1. **计划文件（最稳，零依赖）** —— ``build_import_plan`` 算出「本地有、远端没有」
   的卡牌，导出成 JSON / CSV / ID 列表。可人工照着点，也可喂给别的工具。
2. **浏览器自动化（推荐）** —— ``PlaywrightImporter`` 复用你已登录的浏览器会话，
   在 ``/profile/cards`` 页面上按卡名搜索并逐张添加。默认 **dry-run**，
   加 ``--apply`` 才真正写入。
3. **直接调内部接口（最快，但脆弱）** —— 需要抓包确认请求体，Bestdori 改版即失效，
   本模块不预置，留了 ``--inspect`` 模式帮你抓。

⚠️ 关于选择器
-------------
Bestdori 是 Vue SPA，DOM 结构会随版本变化。本模块不硬编码单一选择器，而是
对每个操作给出一组**候选选择器**，取第一个命中的，抗改版能力更强。若全部
落空，用 ``bdh import-bestdori --inspect`` 打印页面上的输入框/按钮清单，
再把新选择器补进 ``SelectorConfig``。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..inventory.store import Inventory
from ..models import Catalog

log = logging.getLogger(__name__)

BESTDORI_BASE = "https://bestdori.com"
PROFILE_CARDS_URL = f"{BESTDORI_BASE}/profile/cards"
#: 「添加卡牌」是**独立页面**（左侧菜单 Profile -> Add Card），不是 Cards 页上的
#: 标签页。实测打开 /profile/cards/add 才有搜索框；直接打开它，省掉找标签一步。
PROFILE_CARDS_ADD_URL = f"{BESTDORI_BASE}/profile/cards/add"
#: 登录态探针：未登录返回 ``{"result": false, "code": "LOGIN_REQUIRED"}``，
#: 登录后 ``result`` 为 true。比找 DOM 元素可靠 —— SPA 的 DOM 随版本变，API 不会。
LOGIN_PROBE_URL = f"{BESTDORI_BASE}/api/user/me"


# ---------------------------------------------------------------------
# 导入计划
# ---------------------------------------------------------------------


@dataclass
class PlanEntry:
    """计划中的一条待导入项。"""

    card_id: int
    trained: bool
    title: str
    character: str
    rarity: int
    attribute: str

    @property
    def search_text(self) -> str:
        """在 Bestdori 搜索框里输入的文本（卡名最稳，重名时退回「角色+卡名」）。"""
        return self.title or self.character

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImportPlan:
    """一次导入的完整计划。"""

    entries: list[PlanEntry] = field(default_factory=list)
    already_present: list[int] = field(default_factory=list)
    unknown_card_ids: list[int] = field(default_factory=list)
    remote_count: int = 0
    local_count: int = 0

    def __len__(self) -> int:
        return len(self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "localCount": self.local_count,
            "remoteCount": self.remote_count,
            "toImport": len(self.entries),
            "alreadyPresent": len(self.already_present),
            "unknownCardIds": self.unknown_card_ids,
            "entries": [e.to_dict() for e in self.entries],
        }

    def summary(self) -> str:
        return (
            f"本地 {self.local_count} 张，Bestdori 已有 {self.remote_count} 张；"
            f"待导入 {len(self.entries)} 张，已在远端 {len(self.already_present)} 张"
            + (f"，未知卡牌 {len(self.unknown_card_ids)} 个" if self.unknown_card_ids else "")
        )


def build_import_plan(
    inv: Inventory,
    catalog: Catalog,
    remote_keys: Iterable[tuple[int, bool]] | None = None,
    *,
    skip_unconfirmed: bool = False,
) -> ImportPlan:
    """计算需要导入 Bestdori 的卡牌。

    :param remote_keys: 远端已有的 ``(card_id, trained)`` 集合；为 None 时视为远端为空
    :param skip_unconfirmed: 跳过清单里还没人工确认的条目
    """
    remote = set(remote_keys or ())
    plan = ImportPlan(local_count=len(inv), remote_count=len(remote))

    for oc in inv.all():
        if skip_unconfirmed and not oc.confirmed:
            continue
        card = catalog.card(oc.card_id)
        if card is None:
            plan.unknown_card_ids.append(oc.card_id)
            continue
        if (oc.card_id, oc.trained) in remote:
            plan.already_present.append(oc.card_id)
            continue
        plan.entries.append(
            PlanEntry(
                card_id=oc.card_id,
                trained=oc.trained,
                title=card.title(catalog.settings),
                character=catalog.character_name(card),
                rarity=card.rarity,
                attribute=card.attribute,
            )
        )
    return plan


# ---------------------------------------------------------------------
# 浏览器自动化
# ---------------------------------------------------------------------


@dataclass
class SelectorConfig:
    """Bestdori 页面选择器（按候选顺序尝试，取第一个命中的）。

    实测（2026-09，未登录 headless 探测）：``/profile/cards/add`` 页面在**未登录**
    时输入框为 0 个 —— SPA 要等登录后才会渲染操作界面，所以登录后的选择器
    只能靠真实登录校准。这里保留多组候选，全部落空时用 ``--inspect`` /
    「校准选择器」按钮打印页面元素。
    """

    #: 首次访问的欢迎弹窗关闭按钮 —— 不关掉它会挡住页面所有交互
    welcome_close: list[str] = field(
        default_factory=lambda: [
            "button:has-text('Close')",
            "button:has-text('閉じる')",
            ".modal button",
            "[class*='modal'] button",
            "[aria-label='Close']",
        ]
    )
    #: 卡牌搜索输入框
    search_input: list[str] = field(
        default_factory=lambda: [
            "input[type='search']",
            "input[placeholder*='earch']",
            "input[placeholder*='搜索']",
            "input[type='text']",
            "input",
        ]
    )
    #: 搜索结果里可点击的卡牌项
    result_item: list[str] = field(
        default_factory=lambda: [
            ".card-item",
            "[class*='cardItem']",
            "[class*='selection'] img",
            ".my-selection-interface img",
        ]
    )
    #: 保存按钮
    save_button: list[str] = field(
        default_factory=lambda: [
            "button:has-text('Save')",
            "button:has-text('保存')",
            "[class*='save']",
        ]
    )


@dataclass
class ImportOutcome:
    """自动化导入的结果。"""

    attempted: int = 0
    succeeded: int = 0
    failed: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True
    #: 生成计划时拿不到远端列表，实际打开页面后才从数据里识别出来的"已有卡牌"
    skipped_remote: int = 0

    def summary(self) -> str:
        mode = "试运行（未写入）" if self.dry_run else "实际写入"
        extra = f"，已在远端跳过 {self.skipped_remote}" if self.skipped_remote else ""
        return f"{mode}：尝试 {self.attempted}，成功 {self.succeeded}{extra}，失败 {len(self.failed)}"


class PlaywrightImporter:
    """用 Playwright 复用登录态，在 Bestdori 上批量登记卡牌。

    需要 ``pip install playwright && playwright install chromium``。
    """

    def __init__(
        self,
        selectors: SelectorConfig | None = None,
        *,
        user_data_dir: Path | None = None,
        headless: bool = False,
        slow_mo: int = 60,
        interactive: bool = True,
        keep_open_ms: int = 20000,
        login_timeout: float = 300.0,
    ) -> None:
        """
        :param interactive: True 时用 ``input()`` 等待用户在终端登录；GUI 里
            没有 stdin 可读，应传 False，改为轮询等待登录完成。
        :param keep_open_ms: 流程结束后浏览器保持打开的毫秒数，方便人工核对。
        :param login_timeout: 非交互模式下等待登录的最长秒数。
        """
        self.sel = selectors or SelectorConfig()
        self.user_data_dir = user_data_dir or (Path.home() / ".bestdori-helper" / "browser")
        self.headless = headless
        self.slow_mo = slow_mo
        self.interactive = interactive
        self.keep_open_ms = keep_open_ms
        self.login_timeout = login_timeout

    # ---- 内部工具 ----------------------------------------------------

    @staticmethod
    def _first(page, candidates: list[str], timeout: float = 2500):
        """依次尝试候选选择器，返回第一个可见元素。"""
        for sel in candidates:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=timeout)
                return loc, sel
            except Exception:  # noqa: BLE001 - 逐个尝试，失败就换下一个
                continue
        return None, None

    def _dismiss_welcome(self, page, say: Callable[[str], None]) -> None:
        """关掉首次访问的欢迎弹窗 —— 它会挡住页面上的所有交互。

        实测（未登录 headless 探测）：首次打开 Bestdori 会弹 "Welcome to
        Bestdori!" 语言/服务器选择框，必须先点 Close。
        """
        btn, sel = self._first(page, self.sel.welcome_close, timeout=1500)
        if btn is not None:
            say(f"关闭欢迎弹窗（{sel}）")
            btn.click()
            page.wait_for_timeout(600)

    def _login_state(self, page) -> dict[str, Any] | None:
        """探一次登录态；已登录返回用户数据，未登录返回 ``None``。

        用 ``/api/user/me`` 而不是找 DOM：SPA 的页面结构随版本变，
        API 的 ``{"result": false, "code": "LOGIN_REQUIRED"}`` 是实测确认的。
        """
        try:
            resp = page.request.get(LOGIN_PROBE_URL, timeout=10000)
            data = resp.json()
        except Exception:  # noqa: BLE001 - 网络抖动按未登录处理，下一轮再试
            return None
        if isinstance(data, dict) and data.get("result") is True:
            return data
        return None

    def _wait_logged_in(self, page, say: Callable[[str], None]) -> dict[str, Any]:
        """轮询登录探针直到登录成功或超时。"""
        user = self._login_state(page)
        if user is not None:
            say("已是登录状态 ✓")
            return user
        say(
            f"⚠️ 还没有登录。请在弹出的浏览器里登录 Bestdori"
            f"（最多等 {int(self.login_timeout)} 秒，登录成功后会自动继续）…"
        )
        waited = 0.0
        step = 4.0
        while waited < self.login_timeout:
            page.wait_for_timeout(int(step * 1000))
            waited += step
            user = self._login_state(page)
            if user is not None:
                say(f"检测到登录成功（等了 {int(waited)} 秒），继续…")
                return user
            say(f"    等待登录中… {int(waited)}s / {int(self.login_timeout)}s")
        raise RuntimeError(
            "等待登录超时。请先在弹出的浏览器里登录 Bestdori，然后重新执行。"
        )

    @staticmethod
    def _extract_card_keys(obj: Any, out: set) -> None:
        """递归找 ``{"cardId": <int>, ...}`` 形状的对象，收集 ``(cardId, trained)``。

        Bestdori 用户卡牌数据的 API 结构没有官方文档，这里不做硬编码假设：
        把页面加载过程中收到的 JSON 全部递归搜一遍，凡是有 ``cardId`` 整型
        字段的对象都算。宁可多算（Bestdori 对重复添加会去重），不要漏算。
        """
        if isinstance(obj, dict):
            cid = obj.get("cardId")
            if isinstance(cid, int) and not isinstance(cid, bool):
                out.add((cid, bool(obj.get("trained", False))))
            for v in obj.values():
                PlaywrightImporter._extract_card_keys(v, out)
        elif isinstance(obj, list):
            for v in obj:
                PlaywrightImporter._extract_card_keys(v, out)

    def _capture_remote(self, page, say: Callable[[str], None]) -> set:
        """挂上 JSON 响应监听，等 SPA 拉完用户数据后提取远端已有的卡牌。

        :func:`build_import_plan` 拿不到远端列表（那需要登录态），所以生成计划时
        只能按"远端为空"算。这里在已登录的浏览器里补上这一步：所有 JSON 响应
        收下来找 cardId 模式，找到的作为"已在远端"过滤掉，避免重复导入。
        """
        captured: list[Any] = []
        skip = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2",
                ".css", ".js", ".ico", ".mp3", ".svg")

        def on_response(resp) -> None:  # noqa: ANN001 - playwright 回调
            try:
                url = resp.url
                if url.lower().split("?")[0].endswith(skip):
                    return
                if "json" not in resp.headers.get("content-type", ""):
                    return
                captured.append(resp.json())
            except Exception:  # noqa: BLE001 - 回调里绝不能抛
                pass

        page.on("response", on_response)
        page.wait_for_timeout(6000)  # 等 SPA 把用户数据拉完
        remote: set = set()
        for data in captured:
            self._extract_card_keys(data, remote)
        say(f"从页面数据中识别到远端已有卡牌 {len(remote)} 张")
        return remote

    def _require_playwright(self):
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "未安装 Playwright。请执行：\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            ) from e
        return sync_playwright

    # ---- 主流程 ------------------------------------------------------

    def run(
        self,
        plan: ImportPlan,
        *,
        apply: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> ImportOutcome:
        """执行导入。

        :param apply: False 为试运行（只走流程不提交保存）
        """
        say = progress or (lambda _m: None)
        sync_playwright = self._require_playwright()
        outcome = ImportOutcome(dry_run=not apply)

        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(self.user_data_dir),
                headless=self.headless,
                slow_mo=self.slow_mo,
                viewport={"width": 1440, "height": 900},
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                say("打开 Bestdori「添加卡牌」页面…")
                page.goto(PROFILE_CARDS_ADD_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)

                self._dismiss_welcome(page, say)
                self._wait_logged_in(page, say)
                self._dismiss_welcome(page, say)  # 登录后欢迎弹窗可能再弹一次

                # 生成计划时拿不到远端列表（build_import_plan 的 remote_keys=None
                # 表示"按远端为空算"）。现在已登录，把页面数据里的远端卡牌抓出来
                # 做增量过滤，避免把已有的卡重复添加一遍。
                if plan.remote_count == 0:
                    remote = self._capture_remote(page, say)
                    if remote:
                        before = len(plan.entries)
                        plan.entries = [
                            e for e in plan.entries
                            if (e.card_id, e.trained) not in remote
                        ]
                        outcome.skipped_remote = before - len(plan.entries)
                        if outcome.skipped_remote:
                            say(f"已在 Bestdori 上，跳过 {outcome.skipped_remote} 张")
                    if not plan.entries:
                        say("清单里的卡都已经在 Bestdori 上了，无需导入。")
                        return outcome

                box, box_sel = self._first(page, self.sel.search_input)
                if box is None:
                    raise RuntimeError(
                        "找不到搜索框。请用 --inspect 查看页面元素，并把选择器补进 SelectorConfig。"
                    )
                say(f"使用搜索框 {box_sel}")

                for i, entry in enumerate(plan.entries, 1):
                    outcome.attempted += 1
                    say(f"[{i}/{len(plan.entries)}] {entry.character} · {entry.search_text}")
                    try:
                        box.click()
                        box.fill("")
                        box.type(entry.search_text, delay=25)
                        page.wait_for_timeout(900)

                        item, item_sel = self._first(page, self.sel.result_item, timeout=3000)
                        if item is None:
                            raise RuntimeError("搜索结果里没找到可点击的卡牌项")
                        item.click()
                        page.wait_for_timeout(250)
                        outcome.succeeded += 1
                    except Exception as e:  # noqa: BLE001 - 单张失败继续下一张
                        outcome.failed.append({"cardId": entry.card_id, "error": str(e)})
                        log.warning("添加失败 card=%s: %s", entry.card_id, e)

                if apply and outcome.succeeded:
                    save, save_sel = self._first(page, self.sel.save_button)
                    if save is not None:
                        say(f"点击保存（{save_sel}）")
                        save.click()
                        page.wait_for_timeout(1500)
                    else:
                        say("⚠️ 没找到保存按钮。Bestdori 可能已自动保存，请到页面上确认。")
                elif not apply:
                    say("试运行结束，未点击保存。确认无误后加 --apply 重新执行。")

                if self.keep_open_ms > 0:
                    say(f"浏览器保持打开 {self.keep_open_ms // 1000} 秒，方便你核对结果…")
                    page.wait_for_timeout(self.keep_open_ms)
            finally:
                ctx.close()

        return outcome

    # ---- 校准辅助 ----------------------------------------------------

    def inspect(self, auto_close_ms: int = 0) -> dict[str, Any]:
        """打开页面并把可交互元素、API 请求打印出来，用于校准。

        :param auto_close_ms: 大于 0 时自动关闭浏览器（GUI 用），
            否则阻塞等待回车（终端用）。

        除了 DOM 元素，这里还会抓页面加载过程中发出的**全部 XHR/fetch 请求**：
        Bestdori 是 SPA，「我的卡牌」的数据一定来自某个登录态 API —— 找到它，
        才能做出"排除远端已有卡牌"的增量导入（现在 :func:`build_import_plan`
        拿不到远端列表，只能把清单全量当待导入）。
        """
        sync_playwright = self._require_playwright()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(self.user_data_dir), headless=False, viewport={"width": 1440, "height": 900}
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            requests: list[tuple[int, str]] = []

            def _on_response(resp) -> None:  # noqa: ANN001 - playwright 回调
                try:
                    requests.append((resp.status, resp.url))
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", _on_response)
            try:
                page.goto(PROFILE_CARDS_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                info = page.evaluate(
                    """() => ({
                        url: location.href,
                        title: document.title,
                        inputs: [...document.querySelectorAll('input')].map(e => ({
                            type: e.type, placeholder: e.placeholder, cls: e.className
                        })),
                        buttons: [...document.querySelectorAll('button')].map(e => ({
                            text: (e.innerText||'').trim().slice(0,40), cls: e.className
                        })).slice(0, 60),
                        classes: [...new Set([...document.querySelectorAll('[class]')]
                            .flatMap(e => String(e.className).split(/\\s+/))
                            .filter(c => c && /card|selection|search|tab|save|profile/i.test(c)))].slice(0, 80),
                    })"""
                )
                # 只留数据类请求（去掉静态资源），并优先展示带 user/profile/card 字样的
                skip = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".woff2", ".woff",
                        ".css", ".js", ".ico", ".mp3", ".json.gz")
                data_reqs = [u for _s, u in requests if not u.lower().endswith(skip)]
                info["apiRequests"] = data_reqs[:150]
                info["apiUserCard"] = [
                    u for u in data_reqs
                    if any(k in u.lower() for k in ("user", "profile", "card", "account"))
                ][:60]
                print(json.dumps(info, ensure_ascii=False, indent=2))
                if auto_close_ms > 0:
                    print(f"（{auto_close_ms // 1000} 秒后自动关闭浏览器）")
                    page.wait_for_timeout(auto_close_ms)
                elif self.interactive:
                    input("查看完后按回车关闭浏览器…")
                return info
            finally:
                ctx.close()
