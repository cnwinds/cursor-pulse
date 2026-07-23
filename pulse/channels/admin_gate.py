from __future__ import annotations


def is_dingtalk_admin(user_id: str, admin_ids: list[str] | set[str] | None) -> bool:
    """钉钉侧管理员判定。

    空列表表示「未配置任何管理员」→ 无人拥有管理员权限。
    旧行为「空列表=全员管理员」已废弃，避免生产误配。
    """
    if not admin_ids:
        return False
    return user_id in set(admin_ids)
