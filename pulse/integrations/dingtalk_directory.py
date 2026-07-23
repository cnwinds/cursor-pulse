from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable

import requests

from pulse.config import AppConfig
from pulse.storage.models import Member
from pulse.storage.repository import Repository

logger = logging.getLogger(__name__)

OAPI_BASE = "https://oapi.dingtalk.com"


class DingTalkDirectoryClient:
    """钉钉通讯录读取（部门用户 + 主管关系）。"""

    def __init__(self, get_access_token: Callable[[], str]):
        self._get_access_token = get_access_token

    def _post_topapi(self, path: str, body: dict) -> dict:
        token = self._get_access_token()
        response = requests.post(
            f"{OAPI_BASE}{path}",
            params={"access_token": token},
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errcode", 0) != 0:
            raise RuntimeError(f"钉钉 API 错误: {payload.get('errmsg', payload)}")
        return payload

    def list_users_in_dept(self, dept_id: int) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        cursor = 0
        while True:
            payload = self._post_topapi(
                "/topapi/v2/user/list",
                {"dept_id": dept_id, "cursor": cursor, "size": 100},
            )
            result = payload.get("result") or {}
            users.extend(result.get("list") or [])
            if not result.get("has_more"):
                break
            cursor = int(result.get("next_cursor") or 0)
        return users

    def get_user(self, userid: str) -> dict[str, Any]:
        payload = self._post_topapi(
            "/topapi/v2/user/get",
            {"userid": userid, "language": "zh_CN"},
        )
        return payload.get("result") or {}

    def get_userid_by_unionid(self, unionid: str) -> str:
        payload = self._post_topapi("/topapi/user/getbyunionid", {"unionid": unionid})
        result = payload.get("result") or {}
        userid = result.get("userid")
        if not userid:
            raise RuntimeError(f"钉钉未返回 userid: {payload}")
        return str(userid)

    def get_department(self, dept_id: int) -> dict[str, Any]:
        payload = self._post_topapi(
            "/topapi/v2/department/get",
            {"dept_id": dept_id, "language": "zh_CN"},
        )
        return payload.get("result") or {}

    def list_sub_departments(self, dept_id: int) -> list[dict[str, Any]]:
        payload = self._post_topapi(
            "/topapi/v2/department/listsub",
            {"dept_id": dept_id, "language": "zh_CN"},
        )
        return list(payload.get("result") or [])


def collect_all_directory_users(
    client: DingTalkDirectoryClient,
    root_dept_id: int,
) -> list[dict[str, Any]]:
    """遍历部门树，收集全部用户（按 userid 去重）。"""
    seen: set[str] = set()
    users: list[dict[str, Any]] = []

    def walk(dept_id: int) -> None:
        for user in client.list_users_in_dept(dept_id):
            userid = str(user.get("userid") or "")
            if not userid or userid in seen:
                continue
            seen.add(userid)
            users.append(user)
        for sub in client.list_sub_departments(dept_id):
            sub_id = sub.get("dept_id")
            if sub_id is not None:
                walk(int(sub_id))

    walk(root_dept_id)
    return users


def make_directory_client(config: AppConfig) -> DingTalkDirectoryClient:
    from pulse.channels.dingtalk.messenger import DingTalkMessenger

    messenger = DingTalkMessenger(config)
    return DingTalkDirectoryClient(messenger.get_access_token)


def search_directory_by_name(
    client: DingTalkDirectoryClient,
    query: str,
    *,
    root_dept_id: int = 1,
    limit: int = 50,
    max_workers: int = 6,
    timeout_sec: float = 25.0,
) -> list[dict[str, Any]]:
    """按姓名模糊搜索通讯录用户（并行遍历部门树）。"""
    q = query.strip()
    if not q:
        return []

    deadline = time.monotonic() + timeout_sec
    results: list[dict[str, Any]] = []
    seen_users: set[str] = set()
    seen_depts: set[int] = set()
    pending_depts: list[tuple[int, str]] = []
    lock = threading.Lock()
    done = False

    def scan_dept(dept_id: int, dept_name: str) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
        matched: list[dict[str, Any]] = []
        for user in client.list_users_in_dept(dept_id):
            userid = str(user.get("userid") or "")
            name = str(user.get("name") or "")
            if userid and q in name:
                matched.append({**user, "department_name": dept_name})
        children: list[tuple[int, str]] = []
        for sub in client.list_sub_departments(dept_id):
            sub_id = sub.get("dept_id")
            if sub_id is None:
                continue
            children.append((int(sub_id), str(sub.get("name") or sub_id)))
        return matched, children

    root = client.get_department(root_dept_id)
    root_name = str(root.get("name") or root_dept_id)
    pending_depts.append((root_dept_id, root_name))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures: set = set()
        while (pending_depts or futures) and not done and time.monotonic() < deadline:
            while pending_depts and len(futures) < max_workers * 2 and not done:
                dept_id, dept_name = pending_depts.pop(0)
                if dept_id in seen_depts:
                    continue
                seen_depts.add(dept_id)
                futures.add(pool.submit(scan_dept, dept_id, dept_name))

            if not futures:
                break

            remaining = max(0.1, deadline - time.monotonic())
            finished, futures = wait(futures, timeout=remaining, return_when=FIRST_COMPLETED)
            for fut in finished:
                try:
                    matched, children = fut.result()
                except Exception as exc:
                    logger.warning("通讯录部门扫描失败: %s", exc)
                    continue
                with lock:
                    for user in matched:
                        userid = str(user.get("userid") or "")
                        if not userid or userid in seen_users:
                            continue
                        seen_users.add(userid)
                        results.append(user)
                        if len(results) >= limit:
                            done = True
                            break
                    if done:
                        break
                    for child_id, child_name in children:
                        if child_id not in seen_depts:
                            pending_depts.append((child_id, child_name))

    return results


def list_directory_tree_children(
    repo: Repository,
    client: DingTalkDirectoryClient,
    dept_id: int,
) -> dict[str, Any]:
    """返回某部门下的子部门与用户（用于树形懒加载）。"""
    dept_info = client.get_department(dept_id)
    dept_name = dept_info.get("name") or str(dept_id)
    children: list[dict[str, Any]] = []

    for sub in client.list_sub_departments(dept_id):
        sub_id = sub.get("dept_id")
        if sub_id is None:
            continue
        children.append(
            {
                "id": f"dept:{sub_id}",
                "type": "department",
                "dept_id": int(sub_id),
                "label": sub.get("name") or str(sub_id),
                "is_leaf": False,
            }
        )

    for user in client.list_users_in_dept(dept_id):
        userid = str(user.get("userid") or "")
        if not userid:
            continue
        name = user.get("name") or userid
        member = repo.get_or_create_member(userid, name)
        member.display_name = name
        member.department_name = dept_name
        member.employment_status = "active"
        children.append(
            {
                "id": f"user:{userid}",
                "type": "user",
                "label": name,
                "is_leaf": True,
                "member_id": member.id,
                "dingtalk_user_id": userid,
                "department_name": dept_name,
                "portal_status": member.portal_status,
            }
        )

    repo.session.flush()
    return {
        "dept_id": dept_id,
        "label": dept_name,
        "children": children,
    }


def find_users_by_names(
    client: DingTalkDirectoryClient,
    names: list[str],
    *,
    root_dept_id: int = 1,
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """按姓名在通讯录中查找用户。

    返回 (name -> user, 未找到姓名, 重名姓名)。
    """
    wanted = [n.strip() for n in names if n.strip()]
    by_name: dict[str, list[dict[str, Any]]] = {name: [] for name in wanted}
    for user in collect_all_directory_users(client, root_dept_id):
        name = str(user.get("name") or "").strip()
        if name in by_name:
            by_name[name].append(user)

    found: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for name in wanted:
        matches = by_name[name]
        if not matches:
            missing.append(name)
        elif len(matches) > 1:
            ambiguous.append(name)
        else:
            found[name] = matches[0]
    return found, missing, ambiguous


def import_ai_members_by_names(
    session,
    team_id: str,
    repo: Repository,
    config: AppConfig,
    names: list[str],
    *,
    client: DingTalkDirectoryClient | None = None,
    dept_id: int | None = None,
) -> dict[str, Any]:
    """从钉钉按姓名同步成员并授予 ai_member 角色。"""
    from pulse.web.portal import grant_portal_role

    if client is None:
        from pulse.channels.dingtalk.messenger import DingTalkMessenger

        messenger = DingTalkMessenger(config)
        client = DingTalkDirectoryClient(messenger.get_access_token)

    root_dept = dept_id if dept_id is not None else config.dingtalk.sync_root_dept_id
    found, missing, ambiguous = find_users_by_names(client, names, root_dept_id=root_dept)

    granted: list[dict[str, str]] = []
    for name, user in found.items():
        userid = str(user.get("userid") or "")
        member = repo.get_or_create_member(userid, name)
        member.display_name = name
        member.employment_status = "active"
        detail = client.get_user(userid)
        dept_ids = detail.get("dept_id_list") or []
        if dept_ids:
            dept_info = client.get_department(int(dept_ids[0]))
            member.department_name = dept_info.get("name") or member.department_name
        manager_userid = detail.get("manager_userid")
        if manager_userid:
            member.manager_dingtalk_user_id = str(manager_userid)
            manager = repo.get_member_by_dingtalk_id(str(manager_userid))
            if manager:
                member.manager_member_id = manager.id
        grant_portal_role(
            session,
            team_id,
            userid,
            role="ai_member",
            display_name=name,
        )
        granted.append({"name": name, "dingtalk_user_id": userid, "member_id": member.id})

    return {
        "granted": granted,
        "missing": missing,
        "ambiguous": ambiguous,
    }


def sync_dingtalk_directory(
    repo: Repository,
    config: AppConfig,
    *,
    client: DingTalkDirectoryClient | None = None,
    dept_id: int | None = None,
) -> dict[str, int]:
    """同步钉钉通讯录到 members（部门、主管）。"""
    if client is None:
        from pulse.channels.dingtalk.messenger import DingTalkMessenger

        messenger = DingTalkMessenger(config)
        client = DingTalkDirectoryClient(messenger.get_access_token)

    root_dept = dept_id if dept_id is not None else config.dingtalk.sync_root_dept_id
    users_map: dict[str, tuple[dict[str, Any], str]] = {}

    def walk(dept_id: int, dept_name: str) -> None:
        for user in client.list_users_in_dept(dept_id):
            userid = str(user.get("userid") or "")
            if not userid or userid in users_map:
                continue
            users_map[userid] = (user, dept_name)
        for sub in client.list_sub_departments(dept_id):
            sub_id = sub.get("dept_id")
            if sub_id is None:
                continue
            sub_name = str(sub.get("name") or sub_id)
            walk(int(sub_id), sub_name)

    root_info = client.get_department(root_dept)
    root_name = str(root_info.get("name") or root_dept)
    walk(root_dept, root_name)

    stats = {"fetched": len(users_map), "created": 0, "updated": 0, "managers_linked": 0}

    userid_to_member: dict[str, Member] = {}
    pending_managers: list[tuple[Member, str]] = []

    for userid, (user, dept_name) in users_map.items():
        name = user.get("name") or userid
        member = repo.get_or_create_member(userid, name)
        if member.status == "pending":
            stats["created"] += 1
        else:
            stats["updated"] += 1
        member.display_name = name
        member.department_name = dept_name
        member.employment_status = "active"
        detail = client.get_user(userid)
        manager_userid = detail.get("manager_userid")
        if manager_userid:
            member.manager_dingtalk_user_id = str(manager_userid)
            pending_managers.append((member, str(manager_userid)))
        userid_to_member[userid] = member

    for member, manager_userid in pending_managers:
        manager = userid_to_member.get(manager_userid) or repo.get_member_by_dingtalk_id(
            manager_userid
        )
        if manager:
            member.manager_member_id = manager.id
            stats["managers_linked"] += 1

    repo.session.flush()
    return stats
