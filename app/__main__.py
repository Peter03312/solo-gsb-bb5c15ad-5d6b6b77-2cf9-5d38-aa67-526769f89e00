"""支持 `python -m app` 直接启动开发服务器。"""
import os

from . import create_app

if __name__ == "__main__":
    create_app().run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
