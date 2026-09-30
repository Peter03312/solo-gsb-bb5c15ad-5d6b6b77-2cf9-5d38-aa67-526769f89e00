"""Flask 应用工厂与启动入口。"""
import os

from flask import Flask, jsonify

from . import db


def create_app(test_config=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.from_mapping(
        DATABASE=os.environ.get("FREQ_DB_PATH",
                               os.path.join(os.getcwd(), "data", "freq.db")),
        JSON_AS_ASCII=False,
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    from .routes import bp as api_bp
    app.register_blueprint(api_bp)

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
