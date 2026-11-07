# -*- coding: utf-8 -*-
"""PublishOpsMixin — 发布 / 更新 / 同步编排（从 service.Hub 机械抽出，逻辑未改）。

全项目最核心的一段：门禁 → 媒体本地化 → 平台结构变换 → 适配器 → 落库 → trace。
抽出后 service.py 只剩账号、浏览器池与生命周期三件事。
"""

import json
import random
import time

from service.publishing import db
from service.publishing import gate as aigc_gate
from service.publishing.adapters.base import PlatformError, get_adapter
from service.publishing.browser import browser_thread_run
from service.publishing.observability import TraceContext


def _content_hash(article):
    """内容指纹：update 前比对用（与 service.py 中原实现逐字一致）。"""
    import hashlib
    return hashlib.sha256(
        f"{article['title']}\n{article['content_md']}\n{article.get('summary') or ''}"
        .encode("utf-8")).hexdigest()


class PublishOpsMixin:
    def _localize_media(self, article):
        """把正文图片本地化并改写为稳定 URL；失败保持原样（记日志）。

        只在配置了 PUBLIC_BASE_URL 时生效——平台要能访问到这个地址才有意义。
        """
        from service.publishing import media as media_mod
        if not media_mod.public_base():
            return article
        md = article.get("content_md") or ""
        if "![" not in md and "<img" not in md.lower():
            return article
        from service.publishing import observability as obs
        try:
            new_md, stats = media_mod.rewrite_markdown(
                self.conn, md, on_error=lambda src, e: obs.get_logger(__name__).warning(
                    "图片本地化失败，保留原地址 (%s): %s", src[:120], e))
        except Exception as e:   # aqg: top-level boundary（媒体管线不得阻断发布）
            obs.get_logger(__name__).warning("图片本地化整体失败，按原文发布: %s", e)
            return article
        if stats.get("localized"):
            out = dict(article)
            out["content_md"] = new_md
            out["_media_stats"] = stats
            return out
        return article


    def _prepare_article_for_platform(self, article, platform):
        """按平台做内容结构变换（AST 级）。失败则原文返回，不阻断发布。"""
        from service.publishing import content_transform
        md = article.get("content_md") or ""
        if not md:
            return article
        try:
            out, report = content_transform.prepare_for_platform(md, platform)
        except Exception as e:   # aqg: top-level boundary（变换失败退回原文）
            from service.publishing import observability as obs
            obs.get_logger(__name__).warning("内容变换失败，按原文发布: %s", e)
            return article
        if out != md:
            new = dict(article)
            new["content_md"] = out
            new.setdefault("_transform", []).append(report)
            return new
        return article

    def publish_single(self, article_id, platform, article, account="default",
                       draft_only=False, page_hook=None, settings=None):
        """发布到单个平台并完成落库（jobs/publications 记账）。

        发布模式（settings[platform].mode，2026-10-14 新增）：
          - auto（默认）：全自动发到底，行为与本函数原实现逐字一致；
          - assist：自动打开平台页并**预填**文章/标题/标签，停在提交前交还控制权
            等用户改配置/选项/上传附件，用户自己点发布（任务挂 pending_user，可恢复）；
          - manual：只打开平台新建页，不做任何自动填写，用户全程自选。
        中断模式（assist/manual）与 auto 共用同一套门禁/记账；中断时必须先释放
        浏览器池锁再等用户，否则同账号其它任务会被一直堵住。

        演示模式：直接返回模拟成功（含平台域名下的模拟链接），
        让任务引擎、发布记录、前端全链路真实呈现，只差"真的打到平台"。"""
        if self.demo:
            # 轻延迟：让前端能捕捉到任务 running → ok 的真实流转，而非秒过
            time.sleep(0.8 + random.random() * 0.9)
            from service.publishing.adapters.base import get_adapter as _ga
            _nm = _ga(platform).name
            _demo_hosts = {
                "zhihu": "zhuanlan.zhihu.com/p/", "bilibili": "www.bilibili.com/read/cv",
                "cnblogs": "www.cnblogs.com/demo/p/", "csdn": "blog.csdn.net/demo/article/details/",
                "juejin": "juejin.cn/post/",
                "oschina": "my.oschina.net/demo/blog/", "segmentfault": "segmentfault.com/a/",
                "toutiao": "www.toutiao.com/article/",
            }
            import random as _r
            _pid = str(_r.randint(7000000000000000000, 9999999999999999999))
            _url = "https://" + _demo_hosts.get(platform, "example.com/") + _pid
            db.upsert_publication(self.conn, article_id, platform, account,
                                  post_id=_pid, post_url=_url,
                                  edit_url=_url + "/edit",
                                  status="ok" if not draft_only else "pending",
                                  draft_only=1 if draft_only else 0,
                                  content_hash=_content_hash(article),
                                  published_at=db.now())
            db.finish_job(self.conn,
                          db.add_job(self.conn, "publish", article_id, platform),
                          True, "演示模式：发布成功")
            return {"platform": platform, "ok": True, "status": "ok",
                    "post_id": _pid, "post_url": _url, "edit_url": _url + "/edit",
                    "draft_only": draft_only, "demo": True,
                    "note": f"已模拟发布到《{_nm}》（演示模式）"}
        # 平台级结构化改写（AST 层）：去重复 H1、正文标签行等。
        # 放在媒体本地化之后：图片 URL 已是稳定地址，这里只做结构裁剪。
        article = self._prepare_article_for_platform(article, platform)
        # 模式分流：assist/manual 在提交前交还控制权（不进自动发布主链）
        _ps = (settings or {}).get(platform) or {}
        mode = (_ps.get("mode") or "auto").strip() or "auto"
        if mode in ("assist", "manual"):
            return self._publish_with_handoff(
                article_id, platform, article, account=account,
                draft_only=draft_only, settings=settings, mode=mode)

        # 统一入口：legacy 与任务引擎都走这里（重构第一刀）
        # 共用的唯一发布原语。成功返回结果 dict（platform/ok/status/post_id/...），
        # 失败在完成失败记账后抛异常。
        job = db.add_job(self.conn, "publish", article_id, platform)
        try:
            def _do(ad, page):
                # 平台特有字段从 options 下发（标签/分类/摘要等），
                # 各适配器自行决定用哪些、忽略哪些。
                # 发布面板里手动设置的（settings[platform]）优先于文章字段。
                return ad.publish(page, article, self._publish_options(
                    platform, article, settings, draft_only=draft_only))
            r = self._with_adapter(platform, account, _do, page_hook=page_hook)
            # 体检 B1 修复（QA 标质力 2026-09-21）：适配器返回 error 键时
            # 必须走失败分支，否则"发布未完成"会被静默记成"发布成功"
            if r.get("error"):
                raise PlatformError(str(r["error"]))
            warning = r.get("warning", "")
            # 体检 B10 修复：非用户主动要求的 draft_only（如掘金自动发布
            # 失败回落到草稿）→ 记 pending_human，可程序化列出"哪些草稿
            # 等人点发布"；这类平台不算发布完成，文章状态收敛保持 draft
            status = "ok"
            if r.get("draft_only") and not draft_only:
                status = "pending_human"
            pub_id = db.upsert_publication(
                self.conn, article_id, platform, account,
                post_id=r.get("post_id", ""),
                post_url=r.get("post_url", ""),
                edit_url=r.get("edit_url", ""),
                status=status,
                draft_only=1 if r.get("draft_only") else 0,
                content_hash=_content_hash(article),
                published_at=db.now())
            # 发布后回读（0.15.0）：接口返回成功不等于平台上真有这篇。
            # 拿到 post_id 的已发布内容再查一次，把证据记进 publications。
            # 只在真正"发布完成"时回读——草稿/待人工没有可回读的公开内容，
            # 标记 unsupported 而不是伪造一次验证。
            verify = self._verify_publication(
                pub_id, platform, account, article,
                skip=status != "ok")
            db.finish_job(self.conn, job, True,
                          "发布成功" + (f"（警告: {warning}）" if warning else "")
                          + (f"；{verify}" if verify else ""))
            out = {"platform": platform, "ok": True, "status": status,
                   "post_id": r.get("post_id", ""), "post_url": r.get("post_url", ""),
                   "edit_url": r.get("edit_url", ""),
                   "draft_only": r.get("draft_only", "")}
            # 适配器可能带回额外键，透传（不含内部键）
            out.update({k: v for k, v in r.items()
                        if k not in ("error", "warning", "post_id", "post_url",
                                     "edit_url", "draft_only")})
            if warning:
                out["warning"] = warning
            if verify:
                out["verify"] = verify
            return out
        except Exception as e:
            db.upsert_publication(self.conn, article_id, platform, account,
                                  status="failed", last_error=str(e)[:300])
            db.finish_job(self.conn, job, False, str(e)[:300])
            raise   # aqg: top-level boundary（失败记账后重抛，任务引擎据此分诊）


    def _verify_publication(self, pub_id, platform, account, article, skip=False):
        """发布后回读，把结果落到 publications.verify_state。

        三种结果：
          - 适配器不支持回读 → unsupported（如实记录，不假装验证过）
          - 回读通过        → verified + verified_at 时间戳
          - 回读不通过      → failed + 原因（不改 status：平台上可能确实有这篇，
                              只是我们比对不上，交给用户判断，比直接标失败更诚实）

        回读本身抛异常时**不能让整次发布失败**——发布已经成功了。异常折成
        failed + 原因，与"回读不通过"同一条路径。
        """
        if skip:
            db.mark_verify(self.conn, pub_id, "unsupported", "草稿/待人工，无可回读的公开内容")
            return "回读：跳过（草稿/待人工）"
        try:
            ad = get_adapter(platform)
            pub = {"post_id": self._last_post_id(pub_id), "account": account,
                   "post_url": self._last_post_url(pub_id)}
            if ad.needs_browser:
                # 页面类平台：必须借浏览器线程拿 page。回读失败（浏览器起不来、
                # 登录态过期）一律折成 failed，不能影响已经完成的发布。
                def _do(a, page):
                    return a.read_back_in_page(page, pub, article)

                try:
                    ev = self._with_adapter(platform, account, _do)
                    ok = None if not ev.get("supported") else bool(ev.get("found"))
                    if ok:
                        from service.publishing.adapters.base import (
                            content_evidence, title_evidence)
                        t_ok, t_detail = title_evidence(
                            (article or {}).get("title", ""), ev.get("title", ""))
                        c_ok, c_detail = content_evidence(
                            (article or {}).get("content_md", ""), ev.get("body", ""),
                            check_lead=False)   # 已发布页面必有导航/侧栏，不做追加检测
                        if not (t_ok and c_ok):
                            ok = False
                            ev["detail"] = (t_detail if not t_ok else c_detail)
                        elif not ev.get("detail"):
                            # 适配器自己可能已经给了更详细的通过说明（比如 CSDN
                            # 会补上"状态=公开、标签=..."）。有就保留，别覆盖成
                            # 一句笼统的"标题与正文都对得上"——那会让用户以为
                            # 只验了正文。
                            ev["detail"] = "回读通过：页面上的标题与正文都对得上"
                except Exception as e:
                    ev = {"supported": True, "found": False,
                          "detail": f"页面回读失败：{type(e).__name__}: {str(e)[:150]}"}
                    ok = False
            else:
                ok, ev = ad.verify_readback(pub, article)
        except Exception as e:   # aqg: top-level boundary（回读失败不得影响已完成的发布）
            db.mark_verify(self.conn, pub_id, "failed", f"回读异常：{type(e).__name__}: {e}")
            return f"回读：异常（{type(e).__name__}）"
        if ok is None:
            db.mark_verify(self.conn, pub_id, "unsupported", ev.get("detail", ""))
            return "回读：平台不支持"
        if ok:
            db.mark_verify(self.conn, pub_id, "verified", ev.get("detail", ""))
            return "回读：已确认"
        db.mark_verify(self.conn, pub_id, "failed", ev.get("detail", ""))
        return f"回读：未通过（{ev.get('detail', '')[:80]}）"

    def _last_post_id(self, pub_id):
        row = self.conn.execute(
            "SELECT post_id FROM publications WHERE id=?", (pub_id,)).fetchone()
        return (row["post_id"] if row else "") or ""

    def _last_post_url(self, pub_id):
        row = self.conn.execute(
            "SELECT post_url FROM publications WHERE id=?", (pub_id,)).fetchone()
        return (row["post_url"] if row else "") or ""

    def _publish_with_handoff(self, article_id, platform, article, account="default",
                              draft_only=False, settings=None, mode="assist",
                              timeout=1800):
        """中断模式（assist/manual）：预填（assist）或空白（manual）打开平台页，
        提交前交还控制权，等用户改配置/上传附件/自己点发布。

        返回带 pending_user=True 的结果；任务引擎据 status=="pending_user" 挂起等
        用户，用户处理完在平台上自行发布，回来点「已处理，继续」走既有 resume。

        为什么不能直接复用 assist_open：那条路不带文章上下文（不预填）。这里预填
        后仍要**释放池锁再等**——等待期通常几分钟到几十分钟，持锁会把同账号的
        其它任务全堵死（browser_pool 的 per-key 互斥）。
        """
        ad = get_adapter(platform)
        if not ad.needs_browser:
            # 纯协议平台没有「打开页面等人」这个中间态，退化为自动发布
            settings = dict(settings or {})
            settings[platform] = {**(settings.get(platform) or {}), "mode": "auto"}
            return self.publish_single(article_id, platform, article, account=account,
                                       draft_only=draft_only, settings=settings)

        ps = (settings or {}).get(platform) or {}
        job = db.add_job(self.conn, "publish", article_id, platform)
        opened = {}

        def _fill():
            """在浏览器线程里：拿到页面 → （assist 预填）→ 记 URL 后立刻返回，
            让出控制权（不点发布）。"""
            br = self._acquire(platform, account)   # _acquire 自取池锁，调用方不持锁
            page = br.new_page()
            target = ps.get("question_url") or ad.new_url or ad.home_url
            page.goto(target, timeout=60000, wait_until="domcontentloaded")
            time.sleep(2)
            if mode == "assist" and not ps.get("question_url"):
                # 预填标题（正文/标签等由适配器内的导入机制在 auto 链完成；
                # 中断模式只要用户看得见的标题先落位，其余交给用户）
                try:
                    page.fill('textarea.Input[placeholder*="请输入标题"]',
                             (article.get("title") or "")[:100], timeout=8000)
                except Exception:   # aqg: top-level boundary（预填失败不阻断中断流程）
                    pass
            opened["page"] = page
            opened["url"] = page.url or target

        with self._busy_for(platform, account):
            try:
                browser_thread_run(_fill)
            except Exception as e:   # aqg: top-level boundary（开页失败记 job 后重抛，任务引擎分诊）
                db.finish_job(self.conn, job, False, str(e)[:300])
                raise
        # 锁已释放，浏览器保持打开；交还控制权
        db.upsert_publication(self.conn, article_id, platform, account,
                              status="pending_user", post_url=opened.get("url", ""),
                              edit_url=opened.get("url", ""),
                              last_error="", draft_only=1 if draft_only else 0,
                              updated_at=db.now())
        db.finish_job(self.conn, job, True,
                      f"{mode} 模式：页面已打开（{opened.get('url', '')}），等待用户处理")
        return {
            "platform": platform, "ok": True, "status": "pending_user",
            "pending_user": True,
            "post_id": "", "post_url": "", "edit_url": opened.get("url", ""),
            "message": ("浏览器已打开，等你改配置/传附件并自行点发布"
                        if mode == "assist" else
                        "平台新建页已打开，请自行完成全部填写与发布"),
            "mode": mode,
        }


    def publish(self, article_id, platforms, account="default", draft_only=False,
                page_hook=None, settings=None, human_confirmed=False, mode=None):
        art = self.get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        # 发布模式（2026-10-14）：mode 为全平台默认档；settings[platform].mode 逐平台覆盖。
        if mode:
            merged = {pf: {**((settings or {}).get(pf) or {})} for pf in platforms}
            for pf in platforms:
                merged[pf].setdefault("mode", mode)
            for pf, v in (settings or {}).items():   # 保留未在 platforms 里的配置
                merged.setdefault(pf, v)
            settings = merged
        # AIGC 合规门禁：发布前强制过闸（安全扫描 + 双模型审查 + 人审闸门）。
        # human_confirmed：前端「我已核验」显式确认，允许 AI 源正式发布（留痕 ext）。
        try:
            gate_r = aigc_gate.apply_gate_before_publish(
                art, draft_only=draft_only, human_confirmed=human_confirmed)
        except aigc_gate.GateError as ge:
            # 门禁拒绝：记入 jobs（每个平台各一条），便于 /ai/gate 端点追溯
            for pf in platforms:
                jid = db.add_job(self.conn, "publish", article_id, pf)
                db.finish_job(self.conn, jid, False, f"合规门禁拒绝: {str(ge)[:200]}")
            # 抛领域异常：由 app/exceptions.py 映射为 422；service 不再依赖 Web 框架，
            # MCP / CLI 调用同一路径也能拿到可识别的业务错误
            raise ge
        art = gate_r["article"]   # AI 源已打 AIGC 标识
        # 图片本地化（2026-10-29）：把正文里的外链图抓进 media 库并改写成
        # PUBLIC_BASE_URL 下的稳定地址，避免平台侧防盗链 403 / 签名过期裂图。
        # 未配置 PUBLIC_BASE_URL 时整体跳过（改成本地相对路径只会更糟），
        # 下载失败的单张图保留原 URL，不让一张图阻断整篇发布。
        art = self._localize_media(art)
        # 人工确认留痕：谁在什么时候确认了 AI 稿上线（审计追溯，只写不覆盖已有时点）
        if human_confirmed and aigc_gate.is_ai_sourced(art) and not draft_only:
            try:
                ext = json.loads(art.get("ext") or "{}")
                ext.setdefault("human_confirmed_at", time.time())
                ext["human_confirmed_publish"] = True
                db.update_article(self.conn, article_id,
                                  ext=json.dumps(ext, ensure_ascii=False))
            except Exception:   # aqg: top-level boundary（留痕失败不阻断发布）
                pass  # 留痕失败不阻断发布（已过闸，job/trace 仍有记录）
        if not (art.get("title") or "").strip():
            raise ValueError("文章标题为空，拒绝发布（B19 预检）")
        results = []
        all_ok = True
        # 能力矩阵预检：不支持 publish 的平台在提交时就被拦下，而不是跑到
        # 适配器里抛 NotImplementedError 再被当成"网络错误"重试三次。
        from service.publishing.adapters.base import capability_of
        publishable = []
        for pf in platforms:
            caps = capability_of(pf)
            if not caps.get("publish"):
                results.append({"platform": pf, "ok": False,
                                "error": f"{pf} 未声明发布能力（capabilities.publish=false）"})
                all_ok = False
            else:
                publishable.append(pf)
        platforms = publishable
        # 发布链路 trace：所有平台子步骤串进同一 trace_id，事后看卡点
        with TraceContext("publish", article_id=article_id,
                          platforms=platforms, draft_only=draft_only,
                          account=account, aigc=gate_r.get("aigc_labeled", False)) as tctx:
            for pf in platforms:
                _t0 = time.time()
                try:
                    r = self.publish_single(article_id, pf, art, account=account,
                                            draft_only=draft_only, page_hook=page_hook,
                                            settings=settings)
                except Exception as e:
                    all_ok = False
                    results.append({"platform": pf, "ok": False, "error": str(e)})
                    tctx.step(f"{pf}.publish", dur=time.time() - _t0, ok=False,
                               detail=str(e)[:160])
                else:
                    # 体检 B10 修复（QA 标质力 2026-09-21）：回落草稿（pending_human）
                    # 不算发布完成，文章状态收敛保持 draft
                    # 2026-10-14：pending_user（中断模式等你处理）同理不算完成
                    if r.get("status") in ("pending_human", "pending_user"):
                        all_ok = False
                    results.append(r)
                    tctx.step(f"{pf}.publish", dur=time.time() - _t0, ok=True,
                               detail=str(r.get("post_id", ""))[:80])
                if pf != platforms[-1]:
                    time.sleep(random.uniform(*self.delay_platform))
        # 状态收敛：全部成功才标 published；有失败保持 draft 并留待重试，
        # 避免中间一个平台失败就把整篇文章误标成已发布
        if all_ok and not draft_only:
            db.update_article(self.conn, article_id, status="published")
        return results


    def _publish_options(self, platform, article, settings=None,
                         draft_only=False):
        """把「发布设置」组装成适配器 options。发布与更新共用同一套映射。

        单独抽出来的原因：更新也必须带完整设置（CSDN/知乎的更新弹窗是空的，
        不重填就会把原有标签/专栏/封面/摘要清掉）。两处各写一份必然漂移。
        """
        ps = (settings or {}).get(platform) or {}
        art_tags = [t.strip() for t in (article.get("tags") or "").split(",")
                    if t.strip()]
        set_tags = [t.strip() for t in (ps.get("tags") or "").split(",")
                    if t.strip()]
        tag_list = set_tags or art_tags
        return {
            "draft_only": draft_only,
            "tags": tag_list,                 # 标签：面板设置 > 文章 tags
            "tag_category": tag_list[0] if tag_list else "",
            "category": (ps.get("category") or "").strip() or None,
            "summary": (ps.get("summary") or article.get("summary") or ""),
            "topics": (ps.get("topics") or "").strip(),
            "ai_assist": ps.get("ai_assist", False),
            "event": (ps.get("event") or "").strip(),
            "allow_comment": ps.get("allow_comment", True),
            "question_url": (ps.get("question_url") or "").strip(),
            "mode": (ps.get("mode") or "auto").strip() or "auto",
            "cover": (ps.get("cover") or article.get("cover") or ""),
            "visibility": (ps.get("visibility") or "public").strip(),
            "article_type": (ps.get("article_type") or "original").strip(),
            "creation_statement": (ps.get("creation_statement") or "").strip(),
            "template": (ps.get("template") or "").strip(),
            "backup_gitcode": ps.get("backup_gitcode", False),
            "multiplatform": ps.get("multiplatform", False),
            "column": (ps.get("column") or "").strip(),
            "column_publish": ps.get("column_publish", True),
            "scheduled_at": (ps.get("scheduled_at") or "").strip(),
        }

    def _update_options(self, platform, article, settings=None):
        """更新用的 options：内容字段与发布完全一致（不传 draft_only）。"""
        return self._publish_options(platform, article, settings, draft_only=False)

    def update_single(self, article_id, platform, pub, article, account="default",
                      settings=None):
        """原地更新单个平台实例并完成落库（jobs/publications 记账）。

        演示模式：直接返回更新成功。"""
        if self.demo:
            db.upsert_publication(self.conn, article_id, platform, account,
                                  status="ok", last_error="", draft_only=0,
                                  content_hash=_content_hash(article),
                                  updated_at=db.now())
            db.finish_job(self.conn,
                          db.add_job(self.conn, "update", article_id, platform),
                          True, "演示模式：更新成功")
            return {"platform": platform, "ok": True, "demo": True,
                    "note": "演示模式：模拟更新成功"}
        # legacy update() 循环体与工作流节点 update_instance 共用的唯一更新原语
        # （对称于 publish_single，M5/ADR-007）。pub 携带 post_id/edit_url。
        # 成功返回 {'platform', 'ok': True}；失败在完成失败记账后抛异常。
        # 平台间 sleep 由本层统一控制——
        # 本方法为逐行搬移 legacy 循环体（R3 门禁：行为逐字不变）。
        # 内容没变就不空发：改过的才值得占平台额度（B13 数据层加固）
        pub_hash = pub["content_hash"] if isinstance(pub, dict) else pub["content_hash"]
        if pub_hash == _content_hash(article):
            return {"platform": platform, "ok": True, "skipped": "内容未变化"}

        job = db.add_job(self.conn, "update", article_id, platform)
        try:
            def _do(ad, page):
                # 把发布设置一并传下去：CSDN/知乎的更新弹窗同样是空的，不重填就会
                # 把原有标签/专栏/封面/摘要清掉（2026-11-08 实测）。
                try:
                    return ad.update(page, dict(pub), article,
                                     options=self._update_options(platform, article, settings))
                except TypeError:
                    return ad.update(page, dict(pub), article)
            self._with_adapter(platform, account, _do)
            db.upsert_publication(self.conn, article_id, platform, account,
                                  status="ok", last_error="",
                                  draft_only=0, content_hash=_content_hash(article),
                                  updated_at=db.now())
            db.finish_job(self.conn, job, True, "原地更新成功")
            return {"platform": platform, "ok": True}
        except Exception as e:   # aqg: top-level boundary（失败记账后重抛，publish_single 对称）
            db.upsert_publication(self.conn, article_id, platform, account,
                                  last_error=str(e)[:300])
            db.finish_job(self.conn, job, False, str(e)[:300])
            raise


    def update(self, article_id, platforms=None, account="default"):
        """原地更新：打开各平台的编辑页改内容，改完就是更新，不是新发一篇。

        AIGC 合规（补 F-18）：update 路径同样需过安全检查。
        旧实现只走 publish 过闸，AI 改写后直接 update 会把 AI 内容同步到
        已上线平台，绕过《AIGC 标识办法》与高危词扫描。
        这里补一个简化闸：
          - 内容安全扫描永远开（与 publish 一致）
          - AI 源文章强制打 AIGC 标识
          - 不过 draft_only 闸门（update 是同步变更到已上线发布，不是首次上线）
        """
        art = self.get(article_id)
        if not art:
            raise ValueError(f"文章 {article_id} 不存在")
        # 图片本地化：更新同样是"内容上线"，外链图一样会裂
        art = self._localize_media(art)
        # --- AIGC 合规门禁（与 publish 共享安全检查）---
        if aigc_gate.is_enabled():
            dangerous = aigc_gate.scan_dangerous(art.get("content_md", ""), art.get("title", ""))
            if dangerous:
                raise ValueError(f"合规门禁拒绝同步: 高危内容 {dangerous}")
            # AI 源文章补上 AIGC 标识（幂等）
            if aigc_gate.is_ai_sourced(art):
                art = aigc_gate.add_aigc_label(art, art.get("ai_model", ""))
        pubs = db.get_publications(self.conn, article_id=article_id)
        if platforms:
            pubs = [p for p in pubs if p["platform"] in platforms]
        pubs = [p for p in pubs if p["post_id"] or p["edit_url"]]
        # 能力矩阵：不支持原地更新的平台（头条/开源中国）不能进 update 循环，
        # 否则每个都抛"暂不支持原地更新"，sync_pending 永远返回一堆假失败。
        from service.publishing.adapters.base import capability_of
        skipped = [p["platform"] for p in pubs if not capability_of(p["platform"])["update"]]
        pubs = [p for p in pubs if capability_of(p["platform"])["update"]]
        if not pubs:
            return {"skipped": "没有可原地更新的已发布实例（平台不支持或尚未发布）",
                    "unsupported": skipped}

        results = [{"platform": pf, "ok": True, "skipped": "平台不支持原地更新"}
                   for pf in skipped]
        for p in pubs:
            try:
                r = self.update_single(article_id, p["platform"], p, art,
                                       account=account)
            except Exception as e:   # aqg: top-level boundary（单平台失败记错误行，循环继续）
                results.append({"platform": p["platform"],
                                "ok": False, "error": str(e)})
            else:
                results.append(r)
            # 体检 B12 修复（QA 标质力 2026-09-21）：最后一个平台不再空等 30-90s
            if p is not pubs[-1]:
                time.sleep(random.uniform(*self.delay_article))
        return results

