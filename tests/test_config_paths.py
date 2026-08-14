"""版本化发布的外置配置/状态路径测试。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_paths_can_be_loaded_from_external_env_file():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        env_file = base / "production.env"
        env_file.write_text(
            "\n".join((
                f"MENTOR_KB_DIR={base / 'shared' / 'knowledge_base'}",
                f"MENTOR_CHROMA_DIR={base / 'shared' / 'chroma_db'}",
                f"MENTOR_AUTH_DB={base / 'shared' / 'data' / 'auth.db'}",
                "AUTH_SECRET=external-secret",
            )) + "\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        for key in ("MENTOR_KB_DIR", "MENTOR_CHROMA_DIR", "MENTOR_AUTH_DB", "AUTH_SECRET"):
            env.pop(key, None)
        env["MENTOR_ENV_FILE"] = str(env_file)
        env["PYTHONPATH"] = str(ROOT)
        code = (
            "import json; from core import config; "
            "print(json.dumps({'env': str(config.ENV_FILE), "
            "'kb': str(config.KB_DIR), 'chroma': config.DB_DIR, "
            "'auth': config.AUTH_DB, 'secret': config.auth_secret()}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        values = json.loads(result.stdout)
        assert values == {
            "env": str(env_file),
            "kb": str(base / "shared" / "knowledge_base"),
            "chroma": str(base / "shared" / "chroma_db"),
            "auth": str(base / "shared" / "data" / "auth.db"),
            "secret": "external-secret",
        }


if __name__ == "__main__":
    test_paths_can_be_loaded_from_external_env_file()
    print("ok  test_paths_can_be_loaded_from_external_env_file")
