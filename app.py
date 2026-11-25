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


def get_latin_bold_font():
    # अंग्रेज़ी अक्षरों (NEWS T10 | 23-Nov-2026 10:48 AM) के लिए पूर्ण Latin Bold फ़ॉन्ट चुनना
    preferred_patterns = [
        "/usr/share/fonts/**/NotoSans-Bold.ttf",
        "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/**/LiberationSans-Bold.ttf",
    ]
    for pattern in preferred_patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    # यदि ऊपर वाला न मिले तो Devanagari को छोड़कर कोई भी Bold फ़ॉन्ट लें
    all_bold = [
        f for f in glob.glob("/usr/share/fonts/**/*Bold*.ttf", recursive=True)
        if "Devanagari" not in f
    ]
    if all_bold:
        return all_bold[0]

    fallback = [
        f for f in glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
        if "Devanagari" not in f
    ]
    if fallback:
        return fallback[0]
    raise FileNotFoundError("No Latin TrueType (.ttf) fonts found in /usr/share/fonts")


def escape_ffmpeg_text(text):
    text = re.sub(r'[^\w\s\-\|\:\,\.\(\)\/]', '', text)
    text = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return text.strip()


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "News T10 FFmpeg Renderer is Live"})


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

        download_gdrive_file(image_url, img_path)
        request.files["audio_file"].save(audio_path)

        font_path = get_latin_bold_font()
        vf_filters = [
            "scale=1080:1920:force_original_aspect_ratio=increase",
            "crop=1080:1920",
            "setsar=1"
        ]

        if banner_text:
            # जहाँ भी T 10 हो उसे बिना स्पेस के T10 करना
            clean_banner = re.sub(r'\bT\s+10\b', 'T10', banner_text, flags=re.IGNORECASE)
            safe_text = escape_ffmpeg_text(clean_banner)

            # लाल बैनर के ठीक बीचों-बीच (y=1000) बड़ा और स्पष्ट सफ़ेद बोल्ड टेक्स्ट (fontsize=80)
            vf_filters.append(
                f"drawtext=fontfile='{font_path}':text='{safe_text}':fontcolor=white:fontsize=80:shadowcolor=black@0.6:shadowx=3:shadowy=3:x=(w-text_w)/2:y=990:text_align=center"
            )

        vf_chain = ",".join(vf_filters)

        cmd = [
            "ffmpeg", "-y",
            "-threads", "1",
            "-loop", "1", "-framerate", "2", "-i", img_path,
            "-i", audio_path,
            "-vf", vf_chain,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-r", "15",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
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
