# -*- coding: utf-8 -*-
"""媒体资产 API：图片入库、列表、统计与本地托管服务。

配套 `service/publishing/media.py`。前端编辑器里的图片、Markdown 里的外链图
都会先经过这里，再以稳定 URL 出现在各平台的正文里。
"""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..deps import get_hub

router = APIRouter()

_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"}


class MediaUrlIn(BaseModel):
    url: str


@router.post("/media/upload")
async def media_upload(file: UploadFile = File(...)):
    """上传图片入库（内容寻址去重）。返回稳定 URL。

    校验以**文件内容**为准：扩展名与 Content-Type 都来自客户端，可伪造。
    """
    from service.publishing import media

    ext = Path(file.filename or "").suffix.lower()
    if ext and ext not in _ALLOWED_EXT:
        raise HTTPException(422, f"不支持的图片类型: {ext}")
    data = await file.read()
    if not data:
        raise HTTPException(422, "空文件")
    if len(data) > media.MAX_BYTES:
        raise HTTPException(413, f"图片超过 {media.MAX_BYTES // 1024 // 1024}MB 上限")
    try:
        info = media.sniff_image(data)
    except ValueError as e:
        raise HTTPException(422, str(e))

    asset = media.store_bytes(get_hub().conn, data, info["mime"],
                              source=file.filename or "")
    return {"ok": True, "sha256": asset["sha256"], "url": media.asset_url(asset),
            "bytes": asset["bytes"], "deduped": bool(asset.get("deduped")),
            "format": info["format"], "width": info["width"], "height": info["height"]}


@router.post("/media/ingest")
def media_ingest(body: MediaUrlIn):
    """把远程图片抓取入库（SSRF 防护 + 大小上限），返回稳定 URL。"""
    from service.publishing import media
    try:
        asset = media.ingest_url(get_hub().conn, body.url)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "sha256": asset["sha256"], "url": media.asset_url(asset),
            "bytes": asset["bytes"]}


@router.get("/media")
def media_list(limit: int = 100, offset: int = 0):
    from service.publishing import media
    return media.list_assets(get_hub().conn, limit=limit, offset=offset)


@router.get("/media/stats")
def media_stats():
    """媒体库状态 + 对外地址来源（排查"为什么图片没被改写"）。"""
    from service.publishing import media
    out = media.stats(get_hub().conn)
    out["public_base_source"] = media.base_source()
    out["hint"] = (
        "图片会在发布时改写为 public_base 下的地址；为 none 时只入库不改写"
        "（平台抓不到本机地址）。设置方式：环境变量 PUBLIC_BASE_URL，"
        "或用公网域名/反代地址访问一次本应用即可自动探测。")
    return out


# 注意：/media/{sha}{ext} 必须最后声明，否则会把 /media/stats 当产物名匹配
@router.get("/media/{name}", include_in_schema=False)
def media_file(name: str):
    """本地托管的图片（内容寻址 → 可永久缓存）。"""
    from service.publishing import media
    # 只接受 <64 hex><ext>，防目录穿越
    stem, dot, ext = name.partition(".")
    if not stem or len(stem) != 64 or not all(c in "0123456789abcdef" for c in stem):
        raise HTTPException(404, "资源不存在")
    if f".{ext}" not in _ALLOWED_EXT:
        raise HTTPException(404, "资源不存在")
    path = media.media_root() / stem[:2] / f"{stem}.{ext}"
    if not path.is_file():
        raise HTTPException(404, "资源不存在")
    return FileResponse(str(path), headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })
