import os
import re
import glob
import uuid
import subprocess
import requests
from flask import Flask, request, send_file, jsonify, after_this_request

app = Flask(__name__)


def download_gdrive_file(url, dest_path):
    session = requests.Session()
    response = session.get(url, stream=True, timeout=30)
    content_type = response.headers.get("Content-Type", "")

    if "text/html" in content_type and "drive.google.com" in url:
        file_id_match = re.search(r'[?&]id=([^&]+)', url)
        if file_id_match:
            file_id = file_id_match.group(1)
            confirm_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
            response = session.get(confirm_url, stream=True, timeout=30)

    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)

    with open(dest_path, "rb") as f:
        header = f.read(200)
        if b"<!DOCTYPE html" in header or b"<html" in header:
            raise ValueError(f"Google Drive returned an HTML page instead of media for URL: {url}")


def get_font_path():
    matches = glob.glob("/usr/share/fonts/**/NotoSansDevanagari*Bold*.ttf", recursive=True)
    if not matches:
        matches = glob.glob("/usr/share/fonts/**/NotoSans*Bold*.ttf", recursive=True)
    if matches:
        return matches[0]
    fallback = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    if fallback:
        return fallback[0]
    raise FileNotFoundError("No TrueType (.ttf) fonts found in /usr/share/fonts")


def escape_ffmpeg_text(text):
    # FFmpeg drawtext के लिए विशेष चिह्नों को सुरक्षित करना
    text = re.sub(r'[^\w\s\-\|\:\,\.\(\)\/\u0900-\u097F]', '', text)
    text = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return text.strip()


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "News T 10 FFmpeg Renderer is Live"})


@app.route("/render", methods=["POST"])
def render_video():
    job_id = str(uuid.uuid4())
    img_path = f"/tmp/{job_id}_bg.jpg"
    audio_path = f"/tmp/{job_id}_audio.mp3"
    out_path = f"/tmp/{job_id}_output.mp4"

    @after_this_request
    def cleanup(response):
        for path in [img_path, audio_path, out_path]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        return response

    try:
        image_url = request.form.get("image_url", "").strip()
        banner_text = request.form.get("banner_text", "").strip()

        if not image_url:
            return jsonify({"error": "Missing image_url"}), 400
        if "audio_file" not in request.files:
            return jsonify({"error": "Missing audio_file binary"}), 400

        # 1. बैकग्राउंड इमेज डाउनलोड करें और ऑडियो बाइनरी सेव करें
        download_gdrive_file(image_url, img_path)
        request.files["audio_file"].save(audio_path)

        # 2. FFmpeg फ़िल्टर तैयार करें (1080x1920 स्केलिंग + लाइव लाल बैनर पट्टी)
        font_path = get_font_path()
        vf_filters = [
            "scale=1080:1920:force_original_aspect_ratio=increase",
            "crop=1080:1920",
            "setsar=1"
        ]

        if banner_text:
            safe_text = escape_ffmpeg_text(banner_text)
            vf_filters.append(
                f"drawtext=fontfile='{font_path}':text='{safe_text}':"
                f"fontcolor=white:fontsize=38:box=1:boxcolor=0xCC0000@0.95:boxborderw=22:"
                f"x=(w-text_w)/2:y=1150"
            )

        vf_chain = ",".join(vf_filters)

        # 3. MP4 वीडियो रेंडर करें (तेज़ और साफ़ H.264 + AAC)
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", "25", "-i", img_path,
            "-i", audio_path,
            "-vf", vf_chain,
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            out_path
        ]

        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=150)

        return send_file(out_path, mimetype="video/mp4", as_attachment=True, download_name="news_t10_short.mp4")

    except subprocess.CalledProcessError as e:
        return jsonify({"error": "FFmpeg failed", "details": e.stderr.decode("utf-8", errors="ignore")[-500:]}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
