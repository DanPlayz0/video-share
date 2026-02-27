import os
import subprocess

from settings import HLS_FOLDER


def probe_duration_seconds(input_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        value = (result.stdout or "").strip()
        if not value:
            return 0
        seconds = int(float(value))
        return max(seconds, 0)
    except Exception:
        return 0


def inspect_hls_state(video_id):
    output_dir = os.path.join(HLS_FOLDER, video_id)
    playlist_path = os.path.join(output_dir, "playlist.m3u8")

    if not os.path.isdir(output_dir):
        return {
            "status": "missing",
            "segments_generated": 0,
            "segments_expected": 0,
        }

    generated_segments = sum(
        1 for name in os.listdir(output_dir)
        if name.lower().endswith(".ts")
    )

    if not os.path.exists(playlist_path):
        status = "processing" if generated_segments > 0 else "missing"
        return {
            "status": status,
            "segments_generated": generated_segments,
            "segments_expected": 0,
        }

    try:
        with open(playlist_path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = [line.strip() for line in handle.readlines()]
    except OSError:
        return {
            "status": "processing",
            "segments_generated": generated_segments,
            "segments_expected": 0,
        }

    expected_segments = sum(
        1 for line in lines
        if line and not line.startswith("#") and line.lower().endswith(".ts")
    )
    has_endlist = any(line == "#EXT-X-ENDLIST" for line in lines)

    if expected_segments > 0 and has_endlist and generated_segments >= expected_segments:
        status = "complete"
    elif generated_segments > 0 or expected_segments > 0:
        status = "processing"
    else:
        status = "missing"

    return {
        "status": status,
        "segments_generated": generated_segments,
        "segments_expected": expected_segments,
    }


def convert_to_hls(video_id, input_path):
    output_dir = os.path.join(HLS_FOLDER, video_id)
    os.makedirs(output_dir, exist_ok=True)
    playlist_path = os.path.join(output_dir, "playlist.m3u8")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-f", "hls",
        "-hls_time", "6",
        "-hls_playlist_type", "vod",
        "-hls_segment_filename",
        os.path.join(output_dir, "%03d.ts"),
        playlist_path,
    ]

    subprocess.Popen(cmd)
