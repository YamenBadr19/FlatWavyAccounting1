"""
git_utils.py — GitHub Auto-Update via REST API
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
يرفع الملفات إلى GitHub مباشرة عبر GitHub REST API
بدلاً من git CLI — يعمل في أي بيئة بدون قيود.
"""

import os
import base64
import logging
import asyncio
import aiohttp
from pathlib import Path

logger = logging.getLogger('git_utils')

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO  = os.environ.get('GITHUB_REPO', 'YamenBadr19/FlatWavyAccounting1')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
API_BASE     = "https://api.github.com"

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def _get_file_sha(session: aiohttp.ClientSession, path: str) -> str | None:
    """جلب SHA الحالي للملف (مطلوب للتحديث)."""
    url = f"{API_BASE}/repos/{GITHUB_REPO}/contents/{path}"
    async with session.get(url, params={"ref": GITHUB_BRANCH}) as r:
        if r.status == 200:
            data = await r.json()
            return data.get("sha")
        return None


async def _push_file(session: aiohttp.ClientSession, local_path: str, repo_path: str, message: str) -> bool:
    """يرفع ملفاً واحداً إلى GitHub."""
    try:
        with open(local_path, 'rb') as f:
            content = base64.b64encode(f.read()).decode()
    except Exception as e:
        logger.error(f"فشل قراءة {local_path}: {e}")
        return False

    sha = await _get_file_sha(session, repo_path)

    url = f"{API_BASE}/repos/{GITHUB_REPO}/contents/{repo_path}"
    payload = {
        "message": message,
        "content": content,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    async with session.put(url, json=payload) as r:
        if r.status in (200, 201):
            return True
        err = await r.text()
        logger.error(f"فشل رفع {repo_path}: {r.status} — {err[:200]}")
        return False


async def push_files_async(files: dict[str, str], commit_message: str = "تحديث تلقائي") -> dict:
    """
    يرفع مجموعة ملفات إلى GitHub بشكل متسلسل (لتجنب تعارض SHA).

    المعاملات:
      files          — قاموس {مسار_محلي: مسار_في_المستودع}
      commit_message — رسالة الـ commit
    """
    if not GITHUB_TOKEN:
        return {"success": False, "error": "GITHUB_TOKEN غير موجود في Secrets"}

    results = {"success": True, "pushed": [], "failed": []}

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for local, repo in files.items():
            ok = await _push_file(session, local, repo, commit_message)
            if ok:
                results["pushed"].append(repo)
                logger.info(f"✅ {repo}")
            else:
                results["failed"].append(repo)
                results["success"] = False

    return results


def auto_update_repository(commit_message: str = "تحديث تلقائي من النظام") -> bool:
    """
    يرفع جميع ملفات python-brain إلى GitHub.
    يُستدعى بعد أي تعديل على الكود.
    """
    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN غير موجود في Secrets")
        return False

    brain_dir = Path(__file__).parent
    files = {}

    for f in brain_dir.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        rel = f.relative_to(brain_dir.parent)
        files[str(f)] = str(rel).replace("\\", "/")

    if not files:
        logger.warning("لا توجد ملفات Python للرفع")
        return False

    logger.info(f"رفع {len(files)} ملف إلى {GITHUB_REPO}...")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, push_files_async(files, commit_message))
                result = future.result(timeout=120)
        else:
            result = loop.run_until_complete(push_files_async(files, commit_message))
    except Exception as e:
        logger.error(f"خطأ في رفع الملفات: {e}")
        return False

    if result["pushed"]:
        logger.info(f"✅ تم رفع {len(result['pushed'])} ملف إلى GitHub")
    if result["failed"]:
        logger.warning(f"⚠️ فشل رفع {len(result['failed'])} ملف")

    return result["success"]


async def get_repo_info() -> dict:
    """يُعيد معلومات المستودع من GitHub."""
    if not GITHUB_TOKEN:
        return {"error": "GITHUB_TOKEN غير موجود"}

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        url = f"{API_BASE}/repos/{GITHUB_REPO}"
        async with session.get(url) as r:
            if r.status == 200:
                data = await r.json()
                return {
                    "name":           data.get("name"),
                    "full_name":      data.get("full_name"),
                    "default_branch": data.get("default_branch"),
                    "last_push":      data.get("pushed_at"),
                    "private":        data.get("private"),
                }
            return {"error": f"HTTP {r.status}"}
