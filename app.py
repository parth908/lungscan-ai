import os
import secrets
import subprocess
import time
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
CONFIG_FILE = BASE_DIR / "matlab_config.txt"

UPLOAD_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff"}
MAX_UPLOAD_MB = 25

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.secret_key = secrets.token_hex(32)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def read_config():
    """Reads local MATLAB integration settings; no MATLAB source files are uploaded."""
    if not CONFIG_FILE.exists():
        return {}
    data = {}
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    return data


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    cfg = read_config()
    configured = bool(cfg.get("MATLAB_EXE") and cfg.get("RUNNER_M"))
    return jsonify({
        "ok": True,
        "matlab_configured": configured,
        "message": "MATLAB integration configured" if configured else
                   "App server is running. Complete local MATLAB setup to enable real analysis."
    })


@app.post("/api/analyze")
def analyze():
    ct = request.files.get("ct_scan")
    pet = request.files.get("pet_scan")
    if not ct or not ct.filename or not pet or not pet.filename:
        return jsonify(error="Please select both a CT scan and a PET scan."), 400
    if not allowed_file(ct.filename) or not allowed_file(pet.filename):
        return jsonify(error="Use PNG, JPG, JPEG, BMP, TIF, or TIFF images for this prototype."), 400

    job_id = time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(4)
    ct_name = secure_filename(ct.filename)
    pet_name = secure_filename(pet.filename)
    ct_path = (UPLOAD_DIR / f"{job_id}_CT_{ct_name}").resolve()
    pet_path = (UPLOAD_DIR / f"{job_id}_PET_{pet_name}").resolve()
    ct.save(ct_path)
    pet.save(pet_path)

    cfg = read_config()
    matlab_exe = cfg.get("MATLAB_EXE", "")
    runner_m = cfg.get("RUNNER_M", "")
    if not matlab_exe or not runner_m or not Path(matlab_exe).exists() or not Path(runner_m).exists():
        return jsonify(
            error="Your scans were uploaded, but MATLAB is not connected yet. "
                  "Follow the LOCAL_SETUP.txt instructions to connect your existing model.",
            uploaded=True,
            job_id=job_id
        ), 503

    # The runner is a small adapter you create locally. It must call your existing
    # MATLAB analysis using CT_PATH and PET_PATH environment variables and save results.
    env = os.environ.copy()
    env["LUNG_CT_PATH"] = str(ct_path)
    env["LUNG_PET_PATH"] = str(pet_path)
    env["LUNG_RESULT_DIR"] = str(RESULT_DIR)
    env["LUNG_JOB_ID"] = job_id

    try:
        command = [
            matlab_exe, "-batch",
            f"run('{str(Path(runner_m).resolve()).replace(chr(92), '/') }')"
        ]
        proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return jsonify(error="Analysis timed out after 10 minutes. Check MATLAB and your model.", job_id=job_id), 504
    except Exception as exc:
        return jsonify(error=f"Could not start MATLAB: {exc}", job_id=job_id), 500

    if proc.returncode != 0:
        # Avoid exposing local paths/logs in the browser; keep detailed logs locally.
        log_path = RESULT_DIR / f"{job_id}_matlab.log"
        log_path.write_text((proc.stdout or "") + "\n--- STDERR ---\n" + (proc.stderr or ""), encoding="utf-8")
        return jsonify(error="MATLAB analysis failed. Check the local results folder for the MATLAB log.",
                       job_id=job_id), 500

    result_image = RESULT_DIR / f"{job_id}_result.png"
    result_text = RESULT_DIR / f"{job_id}_result.txt"
    payload = {"ok": True, "job_id": job_id, "message": "MATLAB finished running."}
    if result_image.exists():
        payload["result_image"] = f"/results/{result_image.name}"
    if result_text.exists():
        payload["result_text"] = result_text.read_text(encoding="utf-8", errors="replace")[:5000]
    if not result_image.exists() and not result_text.exists():
        payload["message"] = "MATLAB ran, but no standard result file was created. Adapt the local runner to save result output."
    return jsonify(payload)


@app.get("/results/<path:filename>")
def result_file(filename):
    # Only serve files created in the results folder.
    return send_from_directory(RESULT_DIR, filename)


@app.errorhandler(413)
def too_large(_):
    return jsonify(error=f"Each request must be under {MAX_UPLOAD_MB} MB total."), 413


if __name__ == "__main__":
    # Local network only. Keep Windows Firewall enabled and use only a trusted Wi-Fi network.
    app.run(host="0.0.0.0", port=5000, debug=False)
