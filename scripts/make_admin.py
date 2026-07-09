"""把已注册用户提升为管理员（第一个管理员由此产生）。

用法: python scripts/make_admin.py xxx@zju.edu.cn
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import auth


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("用法: python scripts/make_admin.py <email>")
    email = sys.argv[1].strip().lower()
    auth.init_db()
    user = auth.get_user_by_email(email)
    if not user:
        sys.exit(f"用户不存在：{email}（请先在页面注册）")
    auth.set_role(user["id"], "admin")
    print(f"已将 {email} 设为管理员")


if __name__ == "__main__":
    main()
