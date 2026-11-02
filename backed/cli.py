# -*- coding: utf-8 -*-
"""命令行入口：基于 click 重写。

- 业务命令复用发布引擎 ``service.publishing.service.Hub``；
- ``serve`` 子命令复用 ``app.factory:create_app`` 工厂起服务，确保生命周期与鉴权中间件一致。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import click
from service.publishing.service import Hub


def _hub(headed: bool) -> Hub:
    """按全局 --headed 选项构造 Hub。"""
    return Hub(headless=not headed)


def _echo_json(obj) -> None:
    click.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


@click.group(help="AI 内容中台命令行")
@click.option("--headed", is_flag=True, help="显示浏览器窗口（登录/排错时用）")
@click.pass_context
def cli(ctx: click.Context, headed: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["headed"] = headed


@cli.command(help="扫码/过验证登录（首次必须，之后长期有效）")
@click.option("--platform", required=True)
@click.option("--account", default="default")
@click.option("--timeout", type=int, default=600, help="等登录的秒数")
@click.option("--on-captcha", type=click.Choice(["handoff", "skip", "abort"]),
              default="handoff", help="遇到验证码：handoff=暂停交人工 / skip=跳过 / abort=直接放弃")
@click.pass_context
def login(ctx, platform, account, timeout, on_captcha):
    ok, msg = _hub(ctx.obj["headed"]).login(platform, account, timeout, on_captcha)
    click.echo(("✓ " if ok else "✗ ") + msg)


@cli.command(help="检查平台登录态")
@click.option("--platform", required=True)
@click.option("--account", default="default")
@click.pass_context
def check(ctx, platform, account):
    ok = _hub(ctx.obj["headed"]).check(platform, account)
    click.echo("✓ 已登录" if ok else "✗ 未登录，跑 login")


@cli.command(help="清除登录态（删 profile+cookie 快照，需重新扫码）")
@click.option("--platform", required=True)
@click.option("--account", default="default")
@click.pass_context
def logout(ctx, platform, account):
    r = _hub(ctx.obj["headed"]).logout(platform, account)
    click.echo(f"已清除 {platform} 登录态，删除: {r['removed'] or '无文件'}")


@cli.command(help="查看平台接入说明与验证码处理方式")
@click.option("--platform", required=True)
@click.pass_context
def diagnose(ctx, platform):
    _echo_json(_hub(ctx.obj["headed"]).diagnose(platform))


@cli.command(help="打开页面就地处理验证码（半自动+人工）")
@click.option("--platform", required=True)
@click.option("--account", default="default")
@click.option("--wait", type=int, default=180, help="等人工的秒数")
@click.pass_context
def solve_captcha(ctx, platform, account, wait):
    _echo_json(_hub(ctx.obj["headed"]).solve_captcha(platform, account, wait))


@cli.command(help="浏览器登录博客园并自动抠出 MetaWeblog 令牌写进 config.json")
@click.option("--username", required=True, help="博客园登录用户名/邮箱")
@click.option("--password-stdin", is_flag=True, help="从 stdin 读取登录密码")
@click.option("--account", default="default")
@click.option("--timeout", type=int, default=600)
@click.option("--on-captcha", type=click.Choice(["handoff", "abort"]), default="handoff")
@click.pass_context
def bootstrap_cnblogs(ctx, username, password_stdin, account, timeout, on_captcha):
    if password_stdin:
        password = click.get_text_stream("stdin").read().rstrip("\r\n")
    else:
        password = click.prompt("博客园登录密码", hide_input=True, confirmation_prompt=False)
    r = _hub(ctx.obj["headed"]).bootstrap_cnblogs(
        username, password, account, timeout, on_captcha)
    _echo_json({
        "outcome": r.get("outcome"),
        "config_written": bool(r.get("config_written")),
        "has_credentials": bool(r.get("has_credentials")),
    })


@cli.command(help="导入本地 Markdown")
@click.option("--path", required=True)
@click.option("--tags", default="")
@click.pass_context
def import_md(ctx, path, tags):
    click.echo("导入成功，文章 ID = " + str(_hub(ctx.obj["headed"]).import_md(path, tags=tags)))


@cli.command(help="新建文章")
@click.option("--title", required=True)
@click.option("--content", default="")
@click.option("--tags", default="")
@click.pass_context
def create(ctx, title, content, tags):
    aid = _hub(ctx.obj["headed"]).create(title, content, tags=tags, source="human")
    click.echo("文章 ID = " + str(aid))


@cli.command(help="发布到平台（逗号分隔，如 juejin,csdn）")
@click.option("--id", type=int, required=True)
@click.option("--platforms", required=True, help="逗号分隔的平台名")
@click.option("--draft", is_flag=True, help="只发草稿箱")
@click.pass_context
def publish(ctx, id, platforms, draft):
    rows = _hub(ctx.obj["headed"]).publish(id, platforms.split(","), draft_only=draft)
    _echo_json(rows)


@cli.command(help="原地更新已发布文章")
@click.option("--id", type=int, required=True)
@click.option("--platforms", default="", help="留空表示全部已发布平台")
@click.pass_context
def update(ctx, id, platforms):
    plats = platforms.split(",") if platforms else None
    _echo_json(_hub(ctx.obj["headed"]).update(id, plats))


@cli.command(help="把所有改动同步到已发布平台")
@click.pass_context
def sync(ctx):
    _echo_json(_hub(ctx.obj["headed"]).sync_pending())


@cli.command(help="抓取平台上已有文章列表入库")
@click.option("--platform", required=True)
@click.pass_context
def refresh(ctx, platform):
    _echo_json(_hub(ctx.obj["headed"]).refresh(platform))


@cli.command("ai-write", help="AI 写一篇并入库")
@click.option("--topic", required=True)
@click.option("--style", default="")
@click.option("--words", type=int, default=2000)
@click.option("--tags", default="", help="给 AI 的标签提示")
@click.option("--publish", default="", help="写完顺手发到这些平台，逗号分隔")
@click.option("--model", default="", help="provider:model 或 model 名，不指定走 premium 路由")
@click.option("--provider", default="", help="优先使用的 Provider 名")
@click.pass_context
def ai_write(ctx, topic, style, words, tags, publish, model, provider):
    pub = publish.split(",") if publish else None
    _echo_json(_hub(ctx.obj["headed"]).ai_write(
        topic, style, words, tags, pub,
        model=model or None, preferred_provider=provider or None))


@cli.command("ai-rewrite", help="AI 改写已有文章")
@click.option("--id", type=int, required=True)
@click.option("--instruction", required=True)
@click.option("--publish", default="")
@click.pass_context
def ai_rewrite(ctx, id, instruction, publish):
    pub = publish.split(",") if publish else None
    _echo_json(_hub(ctx.obj["headed"]).ai_rewrite(id, instruction, pub))


@cli.command("ai-polish", help="AI 润色文章")
@click.option("--id", type=int, required=True)
@click.pass_context
def ai_polish(ctx, id):
    _echo_json(_hub(ctx.obj["headed"]).ai_polish(id))


# ==================== CLI: 定时发布 ====================

@cli.command("schedule-add", help="添加定时发布任务")
@click.option("--article-id", type=int, required=True, help="文章 ID")
@click.option("--platforms", required=True, help="逗号分隔的目标平台")
@click.option("--type", "sched_type", type=click.Choice(["once", "daily", "weekly", "cron"]),
              default="once", help="调度类型")
@click.option("--expr", "sched_expr", default="", help="调度表达式：once=YYYY-MM-DD HH:MI / daily=HH:MI / weekly=周几-HH:MI / cron=标准5段cron")
@click.option("--title", default="", help="任务标题")
@click.option("--draft", is_flag=True, help="只发草稿")
@click.pass_context
def schedule_add(ctx, article_id, platforms, sched_type, sched_expr, title, draft):
    sid = _hub(ctx.obj["headed"]).scheduler.create(
        article_id, platforms.split(","), draft_only=draft,
        schedule_type=sched_type, schedule_expr=sched_expr, title=title)
    click.echo(f"定时任务 ID = {sid}")


@cli.command("schedule-list", help="列出全部定时任务")
@click.option("--all", "show_all", is_flag=True, help="包含已禁用的")
@click.pass_context
def schedule_list(ctx, show_all):
    _echo_json(_hub(ctx.obj["headed"]).scheduler.list_all(include_disabled=show_all))


@cli.command("schedule-del", help="删除定时任务")
@click.option("--id", type=int, required=True)
@click.pass_context
def schedule_del(ctx, id):
    _hub(ctx.obj["headed"]).scheduler.delete(id)
    click.echo(f"已删除定时任务 {id}")


@cli.command("schedule-trigger", help="手动立即执行一次定时任务")
@click.option("--id", type=int, required=True)
@click.pass_context
def schedule_trigger(ctx, id):
    _hub(ctx.obj["headed"]).scheduler.trigger_now(id)
    click.echo(f"已触发定时任务 {id}")


# ==================== CLI: 文章版本 ====================

@cli.command("versions", help="列出文章的版本历史")
@click.option("--id", "aid", type=int, required=True)
@click.pass_context
def versions_cmd(ctx, aid):
    from service.publishing.versions import list_versions
    _echo_json(list_versions(_hub(ctx.obj["headed"]).conn, aid))


@cli.command("rollback", help="回滚文章到指定版本")
@click.option("--article-id", type=int, required=True)
@click.option("--version-id", type=int, required=True)
@click.pass_context
def rollback_cmd(ctx, article_id, version_id):
    from service.publishing.versions import rollback
    ok, msg = rollback(_hub(ctx.obj["headed"]).conn, version_id)
    click.echo(("✓ " if ok else "✗ ") + msg)


# ==================== CLI: AI 增强 ====================

@cli.command("ai-translate", help="AI 翻译文章")
@click.option("--id", "aid", type=int, required=True)
@click.option("--lang", default="en", help="目标语言：en/ja/ko/fr/de")
@click.pass_context
def ai_translate_cmd(ctx, aid, lang):
    _echo_json(_hub(ctx.obj["headed"]).ai_translate(aid, lang))


@cli.command("ai-prompts", help="AI 生成文章配图 prompt")
@click.option("--id", "aid", type=int, required=True)
@click.option("--n", type=int, default=3)
@click.pass_context
def ai_prompts_cmd(ctx, aid, n):
    _echo_json(_hub(ctx.obj["headed"]).ai_image_prompts(aid, n))


@cli.command("ai-outline", help="AI 提炼文章大纲")
@click.option("--id", "aid", type=int, required=True)
@click.pass_context
def ai_outline_cmd(ctx, aid):
    _echo_json(_hub(ctx.obj["headed"]).ai_outline(aid))


@cli.command("ai-seo", help="AI 生成 SEO 元数据")
@click.option("--id", "aid", type=int, required=True)
@click.pass_context
def ai_seo_cmd(ctx, aid):
    _echo_json(_hub(ctx.obj["headed"]).ai_seo(aid))


@cli.command("clone", help="克隆文章（标题自动加「副本）」）")
@click.option("--id", "aid", type=int, required=True)
@click.pass_context
def clone_cmd(ctx, aid):
    _echo_json(_hub(ctx.obj["headed"]).clone(aid))


# ==================== CLI: 标签治理 ====================

@cli.command("tags", help="列出热门标签")
@click.option("--limit", type=int, default=30)
@click.pass_context
def tags_cmd(ctx, limit):
    from service.publishing.tags import list_tags
    _echo_json(list_tags(_hub(ctx.obj["headed"]).conn, limit=limit))


@cli.command("tag-sync", help="从文章重建标签统计")
@click.pass_context
def tag_sync_cmd(ctx):
    from service.publishing.tags import sync_from_articles
    _echo_json(sync_from_articles(_hub(ctx.obj["headed"]).conn))


@cli.command("tag-rename", help="重命名标签")
@click.option("--old", required=True)
@click.option("--new", required=True)
@click.pass_context
def tag_rename_cmd(ctx, old, new):
    from service.publishing.tags import rename_tag
    _echo_json(rename_tag(_hub(ctx.obj["headed"]).conn, old, new))


@cli.command(help="中台总览：文章数、发布数、待同步数、账号状态")
@click.pass_context
def status(ctx):
    _echo_json(_hub(ctx.obj["headed"]).status())


@cli.command(help="启动 REST API 服务（复用 api.hub 的 FastAPI app）")
@click.option("--host", default="127.0.0.1")
@click.option("--port", type=int, default=8800)
def serve(host, port):
    import uvicorn
    uvicorn.run("app.factory:create_app", factory=True,
                host=host, port=port, reload=False)


if __name__ == "__main__":
    cli()
