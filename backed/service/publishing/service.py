# -*- coding: utf-8 -*-
"""业务层：把 文章库 × 内置浏览器 × 平台适配器 串起来。

AI 或者 REST API 调的都是这里的方法，不用碰浏览器细节。
"""

import atexit
import json
import os
import time
from pathlib import Path

import hashlib

from service.publishing import db
from service.publishing.tasks import TaskManager
from service.publishing.scheduler import init_scheduler
from service.publishing.versions import init_versions
from service.publishing.adapters.base import PlatformError, get_adapter
from service.publishing.browser_pool import BrowserPoolManager
from service.publishing.ai_service import AIService
from service.publishing.ai_ops import AIOpsMixin
from service.publishing.article_ops import ArticleOpsMixin
from service.publishing.publish_ops import PublishOpsMixin


def _content_hash(article):
    """内容指纹：update 前比对用。标题+正文+摘要任一变化都会触发更新。"""
    return hashlib.sha256(
        f"{article['title']}\n{article['content_md']}\n{article.get('summary') or ''}"
        .encode("utf-8")).hexdigest()
from service.publishing.browser import (BuiltinBrowser, CaptchaPolicy,
                          browser_thread_run)

ROOT = Path(__file__).resolve().parent.parent
# config.json 的文档位置是 backed/config.json（2026-10-14 路径修复；ROOT 继续用于
# 数据/profile 等 service/ 下的既有路径与 import_md 的越权防护边界）
BACKED_ROOT = Path(__file__).resolve().parents[2]

# 导入即注册
from service.publishing.adapters import (  # noqa: F401
    bilibili, cnblogs, csdn, juejin, metaweblog, oschina, segmentfault, toutiao, zhihu, wuyi_cto,
)


class Hub(ArticleOpsMixin, AIOpsMixin, PublishOpsMixin):
    # 无头浏览器实例池上限。复用省掉每次发布 1~3 秒的启动开销；
    # 超出后按 LRU 关最旧的（每个实例是一个常驻 Chromium，别开太多）
    POOL_MAX = 4

    def __init__(self, headless=True):
        self.conn = db.connect()
        self.headless = headless
        # 版本历史：每次 edit/AI 改写前自动存旧版快照（时光机回滚能力）
        init_versions(self.conn)
        # 事件系统：webhook 表初始化
        from service.publishing import events as evt_mod
        evt_mod.init_events(self.conn)
        # 演示模式（DEMO=1）：不开浏览器、不调外部 API，登录/发布/更新/AI 写稿
        # 全部模拟成功，用于产品演示与宣传片录制。任务引擎照常真实运转。
        self.demo = os.environ.get("DEMO") == "1"
        # 统一任务引擎：REST/MCP 的登录、发布、更新、同步全部走它（重构第一刀）
        self.tasks = TaskManager(self, self.conn)
        # 定时发布调度器：伴随 Hub 启停，轮询 scheduled_tasks 表投递到 TaskManager
        self.scheduler = init_scheduler(self)
        # 风控：平台间隔 + 文章间隔，别调太小，被限流了别来找我
        # 演示模式下不需要风控延迟，直接归零让流程顺畅
        self.delay_platform = (0, 0) if self.demo else (8, 20)
        self.delay_article = (0, 0) if self.demo else (30, 90)
        # 遇到验证码时怎么处理：handoff=交人工 / abort=直接放弃
        self.on_captcha = "handoff"
        # 浏览器实例池（LRU + per-key 互斥，见 browser_pool.py）
        self._browser_pool = BrowserPoolManager(headless)
        # 文章 CRUD 仓库（纯 DB 操作，无浏览器/AI 依赖）
        # AI 增强操作（写稿/改写/润色/工具），委托外部函数注入
        self.ai = AIService(
            self.conn, self.demo,
            get_article_fn=self.get,
            create_article_fn=self.create,
            publish_fn=lambda *a, **kw: self.publish(*a, **kw),
            update_fn=lambda *a, **kw: self.update(*a, **kw),
        )
        # 进程退出兜底关浏览器（原来这行写在 _busy_for 的 return 后面，
        # 是永远执行不到的死代码——退出时浏览器从不自动关）
        atexit.register(self.close_all)

    # ------------- 浏览器实例池（委托 _browser_pool）-------------

    def _acquire(self, platform, account):
        """拿（或起）一个 headless 实例（委托 BrowserPoolManager）。"""
        return self._browser_pool._acquire(platform, account)

    def _drop(self, platform, account):
        """实例疑似崩了：踢出池关掉（委托 BrowserPoolManager）。"""
        return self._browser_pool._drop(platform, account)

    def _busy_for(self, platform, account):
        """取（或建）per-key 互斥锁（委托 BrowserPoolManager）。

        回归：0.7.0 中登录/检查/验证码/辅助/发布路径都用
        `with self._busy_for(...)` 包住浏览器操作，但 Hub 从未定义过这个方法
        ——只有 BrowserPoolManager 有。一运行到登录就
        `AttributeError: 'Hub' object has no attribute '_busy_for'`。
        """
        return self._browser_pool._busy_for(platform, account)

    def close_all(self):
        """进程退出前关浏览器 + 停调度器 + 关任务引擎（atexit 兜底）。

        atexit 里任何未捕获异常都会以 "Exception ignored" 噪声打到进程退出输出
        （测试进程退出时临时目录已清理，这里逐个兜住，保证退出干净）。
        """
        try:
            self.scheduler.stop()
        except Exception:   # aqg: top-level boundary（进程退出兜底，逐个吞错）
            pass
        try:
            self.tasks.close()
        except Exception:   # aqg: top-level boundary（同上）
            pass
        try:
            self._browser_pool.close_all_browsers()
        except Exception:   # aqg: top-level boundary（同上）
            pass

    # ---------------- 账号 ----------------

    def login(self, platform, account="default", timeout=600, on_captcha=None):
        """扫码/过验证登录一次，登录态存进本地 profile，之后长期有效。"""
        from service.publishing._account_ops import login as _do_login
        return _do_login(platform, account, timeout,
                         on_captcha or self.on_captcha,
                         self._browser_pool, self._drop,
                         self._busy_for, self.conn, self.demo)

    def _check_auth_settled(self, ad, page, settle=2.0, tries=2):
        """登录态检查（带沉降重试）。

        竞态：goto 等到 domcontentloaded 后 SPA 可能仍在跳转/替换文档，
        立即 evaluate 打在销毁的 context 上会抛异常，被 check_auth 吞掉
        变成假"未登录"（2026-09-22 掘金无头复查连假的教训）。
        首次 False/异常 → 沉降 settle 秒再试一次。
        """
        for i in range(tries):
            try:
                if ad.check_auth(page):
                    return True
            except Exception:
                pass
            if i < tries - 1:
                time.sleep(settle)
        return False

    def check(self, platform, account="default"):
        if self.demo:
            return True
        ad = get_adapter(platform)
        if not ad.needs_browser:
            try:
                ok = ad.check_auth(None)
            except PlatformError:
                ok = False
            db.upsert_account(self.conn, platform, account, "-",
                              "logined" if ok else "offline")
            return ok
        # B3 补洞（2026-09-22）：check 同样要持 per-key 互斥。
        # 存量体检（2026-09-22）：浏览器操作全部经 browser_thread_run 下沉
        # 专属线程——greenlet/循环状态永远单线程，根治 "inside the asyncio
        # loop" 500（线程池复用 + dispatcher 挂起泄漏循环状态的组合病）。
        def _op():
            br = self._acquire(platform, account)
            page = None
            try:
                page = br.new_page()
                # 关键：check_auth 多在页面上下文里 fetch 平台 API。停在 about:blank
                # 上是 null 源跨站请求，SameSite cookie 全被拦 → data:null → 永远
                # 假"未登录"（2026-09-22 掘金复查连假的根因）。先落到平台域再查。
                target = ad.home_url or ad.login_url
                if target:
                    try:
                        page.goto(target, timeout=60000, wait_until="domcontentloaded")
                    except Exception:
                        pass  # 首页打不开不拦着，交给沉降重试兜底
                ok = self._check_auth_settled(ad, page)
                if ok:
                    # 登录态保鲜：检查通过即刷新快照——平台轮换会话后快照
                    # 跟着最新态走（2026-09-22 "一次登录一直记住"要求）
                    try:
                        br.export_auth()
                    except Exception:
                        pass
                # F-16 简化：合并 3 处重复的 upsert_account 为 1 处。
                # 即使弹验证码（CaptchaPolicy.detect），登录态本身仍有效，
                # 统一写 "logined"；未登录才写 "offline"。
                db.upsert_account(self.conn, platform, account, str(br.profile_dir),
                                  "logined" if ok else "offline")
                return ok
            except Exception:
                self._drop(platform, account)   # 页面出事：踢出池，下次用全新的
                raise
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
        with self._busy_for(platform, account):
            return browser_thread_run(_op)

    def logout(self, platform, account="default"):
        """清除某个平台的登录态：删 profile 目录 + auth 快照，账号状态置离线。"""
        from service.publishing._account_ops import logout as _do_logout
        return _do_logout(platform, account, self._browser_pool, self._drop, self.conn)

    def diagnose(self, platform):
        """排查端点：这平台到底该怎么接入、验证码怎么过，一目了然。"""
        from service.publishing.browser import ensure_display
        ad = get_adapter(platform)
        info = {
            "platform": platform, "name": ad.name,
            "需要浏览器": ad.needs_browser,
            "登录页": ad.login_url,
            "反检测": "已启用（隐藏 webdriver / 伪装 WebGL 与硬件指纹 / 拟人输入鼠标）",
        }
        if platform in CaptchaPolicy.API_FIRST:
            info["推荐接入"] = CaptchaPolicy.API_FIRST[platform]
        info["验证码策略"] = (
            "免登 API，不会遇到验证码" if not ad.needs_browser else
            "L1 反检测降低触发 → L2 登录态持久化 → L3 弹了就半自动试一次，不行转人工"
        )
        if ad.needs_browser:
            info["有头显示"] = ensure_display() or "不可用（装 xvfb：apt install -y xvfb）"
        return info

    def assist_open(self, platform, url, timeout=900, account="default"):
        """带登录态的有头浏览器打开平台页——把"去平台互动"接进本机。"""
        from service.publishing._account_ops import assist_open as _do_assist
        return _do_assist(platform, account, url, timeout,
                          self._browser_pool, self._drop, self._busy_for,
                          self.demo, self.conn)

    def bootstrap_cnblogs(self, username, password, account="default",
                          timeout=600, on_captcha="handoff"):
        """浏览器登录博客园，登录后自动抠出 MetaWeblog 三件套写进 config.json。

        这是取代「让用户手贴令牌」的正确路径：账号+密码登一次，登录态存盘，
        设置页里把 endpoint/username/token 自动抠出来，之后发布走纯协议。
        """
        from service.publishing.cnblogs_login import login_browser, extract_metaweblog
        from service.publishing.browser import ensure_display

        ensure_display()
        self._drop("cnblogs", account)   # profile 独占，先释放池里的实例

        def _op():
            br = BuiltinBrowser("cnblogs", account, headless=False)  # 登录必须有头
            try:
                r = login_browser(br, username, password,
                                  on_captcha=on_captcha, timeout=timeout)
                if r.get("outcome") != "success":
                    db.upsert_account(self.conn, "cnblogs", account, str(br.profile_dir),
                                      "offline")
                    return r
                # 登录成功，抠令牌（username 用登录名，别拿 blogApp 冒充）
                page = br.new_page()
                mw = extract_metaweblog(page, save_to=str(BACKED_ROOT / "config.json"),
                                        login_username=username)
                page.close()
                db.upsert_account(self.conn, "cnblogs", account, str(br.profile_dir),
                                  "logined")
                r["metaweblog"] = mw
                return r
            finally:
                br.close()
        return browser_thread_run(_op)

    def solve_captcha(self, platform, account="default", wait=180):
        """打开平台页面，如果弹验证码就地处理：先半自动，不行转人工。"""
        from service.publishing._account_ops import solve_captcha as _do_solve
        return _do_solve(platform, account, wait, self._browser_pool,
                         self._drop, self._busy_for, self.conn)

    def accounts(self):
        return [dict(r) for r in db.list_accounts(self.conn)]

    # ---------------- 文章 ----------------









    # ---------------- 发布 / 更新 ----------------

    def _with_adapter(self, platform, account, fn, page_hook=None):
        """统一入口：需要浏览器的开浏览器，走协议的直接调。
        浏览器从池里拿，常驻复用；页面用完即关（ctx 保留）。
        page_hook(page)：页面就绪后回调（已废弃，保留签名以兼容旧调用），
        必须在浏览器操作线程内调用（greenlet 约束）。"""
        ad = get_adapter(platform)
        if not ad.needs_browser:
            if not ad.check_auth(None):
                raise PlatformError(f"{platform} 凭据无效，检查 config.json")
            return fn(ad, None)

        # B3：同一实例同时只允许一个操作者，等也要等在这（公平串行）。
        # 存量体检（2026-09-22）：操作体下沉专属浏览器线程（greenlet 同线程）。
        with self._busy_for(platform, account):
            def _op():
                page = None
                for attempt in (1, 2):
                    br = self._acquire(platform, account)
                    try:
                        page = br.new_page()
                        # 先把页面带到平台域再发 API：新开的 page 停在 about:blank 上是
                        # null origin，fetch 属于跨域，cookie 带不上还会被 CORS 拦下。
                        # 落到平台自己的页面上，后面的 api_get/api_post 就是同源请求。
                        try:
                            page.goto(ad.home_url, timeout=60000, wait_until="domcontentloaded")
                        except Exception:
                            pass  # 首页打不开不拦着纯 API 调用，尽力继续
                        if not self._check_auth_settled(ad, page):
                            raise PlatformError(f"{platform}({account}) 未登录，先跑 login")
                        if page_hook:
                            try:
                                page_hook(page)
                            except Exception:
                                pass  # 预览挂载失败不影响发布主流程
                        return fn(ad, page)
                    except PlatformError:
                        raise                       # 业务错误（未登录/配置缺失），重试没意义
                    except Exception as e:
                        if attempt == 2:
                            raise
                        # 像浏览器/页面崩了的错误：踢出池换全新实例再试一次
                        # （greenlet 跨线程冲突=池实例被别的线程创建，重建即在当前线程）
                        if any(s in str(e) for s in ("Target closed", "Browser has been closed",
                                                     "Session closed", "浏览器启动失败",
                                                     "Cannot switch to a different thread",
                                                     "greenlet")):
                            # F-17：不再静默吞错——打 warning 记录具体错误类型与堆栈，
                            # 方便事后排查“为什么这个平台的浏览器崩了”。
                            import logging as _logging
                            _logging.getLogger(__name__).warning(
                                "浏览器实例崩溃，踢出池重建 (%s/%s): %s: %s",
                                platform, account, type(e).__name__, str(e)[:200])
                            self._drop(platform, account)
                            continue
                        raise
                    finally:
                        if page is not None:
                            try:
                                page.close()
                            except Exception:
                                pass
                            page = None
            return browser_thread_run(_op)

    # ---------------- AI ----------------

    def _demo_article(self, topic, style="", words=2000, tags_hint=""):
        """演示模式下的 AI 写稿：本地模板生成，不依赖 LLM。"""
        return {
            "title": topic,
            "summary": f"《{topic}》深度解读：从背景到实践，一文讲透（演示文章）。",
            "content_md": (
                f"# {topic}\n\n"
                f"> 这是一篇由 **AI 内容中台** 在演示模式下生成的文章（DEMO 演示）。\n\n"
                f"## 背景\n\n{topic}正在成为内容创作者关注的焦点。"
                f"本文带你从零了解它的来龙去脉、核心概念与落地方法。\n\n"
                f"## 核心要点\n\n"
                f"1. **统一入口**：一个后台管理所有内容平台，不用再逐站登录、逐站发布。\n"
                f"2. **一次登录**：扫码一次，登录态长期复用，之后全自动发布。\n"
                f"3. **一键多投**：选中目标平台，一篇文章同时发往知乎、掘金、CSDN 等。\n"
                f"4. **原地更新**：改一处，已发布文章全平台同步更新，不产生重复内容。\n"
                f"5. **人工兜底**：平台风控或验证码出现时，任务自动转人工，处理后可恢复。\n\n"
                f"## 适用场景\n\n"
                f"个人博客、技术社区运营、自媒体矩阵维护，都可以用它把重复劳动交给自动化。\n\n"
                f"## 写在最后\n\n"
                f"内容中台的价值在于：**把时间还给创作，把重复交给机器**。"
                f"（本文为产品演示而生成，实际发布内容由你的真实文章决定。）\n"
            ),
            "tags": (tags_hint or "AI,内容中台,自动化"),
            "ai_model": "demo-local",
            "ext": "{}",
        }




















    def sync_pending(self, account="default"):
        """把内容变更同步到所有已发布平台——AI 改完文章点这个就完事。
        按文章聚合其全部 pending 平台，一次 update 同步到位，避免逐行漏同步。"""
        rows = db.get_pending_updates(self.conn)
        by_article = {}
        for r in rows:
            by_article.setdefault(r["article_id"], {"title": r["title"], "platforms": []})
            by_article[r["article_id"]]["platforms"].append(r["platform"])
        out = []
        for aid, info in by_article.items():
            platforms = sorted(set(info["platforms"]))
            res = self.update(aid, platforms=platforms, account=account)
            out.append({"article_id": aid, "title": info["title"],
                        "platforms": platforms,
                        "result": res.get("results") if isinstance(res, dict) else res})
        return out

    def refresh(self, platform, account="default", limit=50):
        """把平台上的文章列表抓回来入库，AI 才能"看见账号里有什么"。"""
        if self.demo:
            items = [{
                "post_id": f"demo-{platform}-{i}", "title": f"《{platform} 平台演示文章 {i}》",
                "url": f"https://example.com/{platform}/{i}", "edit_url": "",
                "status": "published", "stats": {"read": 100 + i * 7, "like": 5 + i},
            } for i in range(1, 4)]
            for it in items:
                aid = db.create_article(self.conn, it["title"], "",
                                        status="published", source="import")
                db.upsert_publication(self.conn, aid, platform, account,
                                      post_id=it["post_id"], post_url=it["url"],
                                      edit_url="", status="ok",
                                      stats=json.dumps(it["stats"]), draft_only=0)
            self.conn.commit()
            return {"platform": platform, "count": len(items), "saved": len(items),
                    "items": items, "demo": True}
        def _do(ad, page):
            return ad.list_articles(page, limit=limit)
        items = self._with_adapter(platform, account, _do)
        saved = 0
        for it in items:
            # 已有同 post_id 的更新，否则新建一条孤儿记录等人工关联
            row = self.conn.execute(
                "SELECT * FROM publications WHERE platform=? AND post_id=?",
                (platform, it["post_id"])).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE publications SET edit_url=?, post_url=?, stats=?, status='ok' WHERE id=?",
                    (it.get("edit_url", ""), it.get("url", ""),
                     json.dumps(it.get("stats", {}), ensure_ascii=False), row["id"]))
            else:
                aid = db.create_article(self.conn, it["title"], "",
                                        status="published", source="import")
                db.upsert_publication(self.conn, aid, platform, account,
                                      post_id=it["post_id"], post_url=it.get("url", ""),
                                      edit_url=it.get("edit_url", ""), status="ok",
                                      stats=json.dumps(it.get("stats", {}), ensure_ascii=False),
                                      draft_only=0)
            saved += 1
        self.conn.commit()
        return {"platform": platform, "count": len(items), "saved": saved, "items": items}

    def status(self):
        arts = self.conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"]
        pubs = self.conn.execute("SELECT COUNT(*) c FROM publications").fetchone()["c"]
        ok = self.conn.execute(
            "SELECT COUNT(*) c FROM publications WHERE status='ok'").fetchone()["c"]
        pend = self.conn.execute(
            "SELECT COUNT(*) c FROM publications WHERE status='pending'").fetchone()["c"]
        pub_arts = self.conn.execute(
            "SELECT COUNT(*) c FROM articles WHERE status='published'").fetchone()["c"]
        # published = 已上线的平台实例数；published_articles = 已发布文章数（两个口径别混用）
        return {"articles": arts, "publications": pubs, "published": ok,
                "published_articles": pub_arts,
                "pending_sync": pend, "accounts": self.accounts()}
