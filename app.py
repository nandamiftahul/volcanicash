import os
from flask import Flask, render_template
from config import settings

def create_app():
    app = Flask(__name__)
    app.secret_key = settings.SECRET_KEY

    # === Register API Blueprints ===
    from routes.api_ash import bp as ash_bp
    from routes.api_hysplit import bp as hysplit_bp
    app.register_blueprint(ash_bp)
    app.register_blueprint(hysplit_bp)

    # === Main page ===
    @app.route("/")
    def index():
        return render_template("trajectory_ash3d.html")

    @app.route("/about")
    def about():
        return {
            "app": "Volcanic Trajectory 3D Viewer",
            "debug": settings.DEBUG,
            "api": "NOAA READY HYSPLIT",
            "version": "v1.0.0"
        }

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=settings.PORT, debug=settings.DEBUG)
