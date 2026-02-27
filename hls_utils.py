import os
import subprocess

from settings import HLS_FOLDER


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
