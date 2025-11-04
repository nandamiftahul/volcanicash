# app.py
import os
from flask import Flask, render_template
from dotenv import load_dotenv

# --- Load environment (.env) ---
load_dotenv()

def create_app():
    app = Flask(__name__)

    # === Register Blueprints ===
    from routes.api import bp as api_bp
    app.register_blueprint(api_bp)

    # === Routes untuk halaman utama ===
    @app.route("/")
    def index():
        return render_template("trajectory_ash3d.html")

    # === Info singkat ===
    @app.route("/about")
    def about():
        return {
            "project": "Volcanic Trajectory 3D Viewer",
            "version": "v1.0.0",
            "developer": "Terrindo / BMKG Prototype"
        }

    return app


# === Jalankan langsung (local dev) ===
if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
