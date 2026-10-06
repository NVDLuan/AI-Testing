"""Nhúng QR (nội dung = frame_id) vào từng frame của video."""
from __future__ import annotations

import io
import json
import os
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Optional, Tuple, Union

import qrcode
from PIL import Image

from .config import DEFAULT_LAYOUT, QRLayout
from .ffmpeg import FFmpegEncoder


class QRVideoGenerator:
    """
    Pipeline cho 1 video (1 lần encode, không file tạm):
        1) ffprobe lấy fps (dạng phân số chính xác) + tổng frame
        2) sinh PNG QR cho từng frame_id (0..total-1) trong RAM, stream qua stdin cho ffmpeg
        3) ffmpeg overlay QR lên góc trên-phải video gốc

    Không ghi file tạm nên chạy song song nhiều tiến trình an toàn.
    Nếu stream QR ngắn hơn video, các frame thừa sẽ KHÔNG có QR (eof_action=pass)
    thay vì lặp lại QR cuối (gây sai frame_id).
    """

    def __init__(self, layout: QRLayout = DEFAULT_LAYOUT, use_gpu: Union[bool, str] = "auto"):
        self.layout = layout
        self.use_gpu = use_gpu

    # ------------------------------------------------------------------ steps
    def generate_qr(self, text) -> Image.Image:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=self.layout.qr_box_size,
            border=self.layout.qr_border,
        )
        qr.add_data(text)
        qr.make(fit=True)

        img = qr.make_image(fill="black", back_color="white").get_image()
        size = self.layout.qr_image_size
        return img.resize((size, size), Image.LANCZOS)  # QR nhỏ nhất có thể đọc được

    def generate_qr_png(self, text) -> bytes:
        buf = io.BytesIO()
        self.generate_qr(text).save(buf, format="PNG")
        return buf.getvalue()

    def _blank_png(self) -> bytes:
        img = self.generate_qr(0)
        img.paste(255, (0, 0, *img.size))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def get_video_info(video_path) -> Tuple[Fraction, int]:
        """Trả về (fps, total_frames). Thiếu nb_frames (mkv, ts...) -> đếm packet bằng ffprobe."""
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
               "-select_streams", "v:0", str(video_path)]
        streams = json.loads(subprocess.check_output(cmd)).get("streams", [])
        if not streams:
            raise ValueError(f"Không tìm thấy video stream: {video_path}")
        v = streams[0]

        fps = Fraction(0)
        for key in ("r_frame_rate", "avg_frame_rate"):
            try:
                fps = Fraction(v.get(key, "0/0"))
            except (ValueError, ZeroDivisionError):
                continue
            if fps > 0:
                break
        if fps <= 0:
            raise ValueError(f"Không xác định được FPS: {video_path}")

        total = int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else 0
        if total <= 0:
            total = QRVideoGenerator._count_frames(video_path)
        if total <= 0:
            raise ValueError(f"Không xác định được số frame: {video_path}")
        return fps, total

    @staticmethod
    def get_start_time(video_path) -> float:
        """start_time của video stream (vd. video ghép từ segment có thể bắt đầu ở 0.023s)."""
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=start_time", "-of", "csv=p=0", str(video_path)]
        try:
            return max(float(subprocess.check_output(cmd, text=True).strip().split(",")[0]), 0.0)
        except ValueError:
            return 0.0

    @staticmethod
    def _count_frames(video_path) -> int:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
               "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(video_path)]
        out = subprocess.check_output(cmd, text=True).strip().split(",")[0]
        return int(out) if out.isdigit() else 0

    # ----------------------------------------------------------------- public
    def process_video(self, input_video, output_video="output_with_qr.mp4",
                      use_gpu: Optional[bool | str] = None) -> str:
        if not FFmpegEncoder.is_ffmpeg_available():
            raise RuntimeError("ffmpeg/ffprobe is required but not available on PATH.")

        fps, total = self.get_video_info(input_video)
        # Căn QR thứ k vào frame thứ k: QR bắt đầu tại start_time (video ghép có thể bắt đầu ở 0.023s),
        # sớm hơn nửa frame để không lệch do làm tròn pts; timebase AVTB (µs) để không bị lượng tử hóa theo 1/fps.
        qr_offset = self.get_start_time(input_video) - 0.5 / float(fps)
        m = self.layout.overlay_margin
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(input_video),
            "-f", "image2pipe", "-c:v", "png", "-framerate", str(fps), "-i", "-",
            "-filter_complex",
            f"[1:v]settb=AVTB,setpts=PTS-STARTPTS+({qr_offset:.6f}/TB)[qr];"
            f"[0:v][qr]overlay=W-w-{m}:{m}:eof_action=pass[v]",
            "-map", "[v]", "-map", "0:a?",
            *FFmpegEncoder.encode_args(self._gpu(use_gpu)),
            "-c:a", "copy",
            "-movflags", "faststart",
            str(output_video),
        ]

        print(f"  🔄 Đang nhúng {total} QR codes vào video...")
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for frame_id in range(total):
                proc.stdin.write(self.generate_qr_png(frame_id))
            # Frame trắng kết thúc: giữ QR cuối hiển thị đủ 1 frame (overlay ngắt QR ngay tại pts EOF);
            # nếu video thực tế dài hơn `total`, frame thừa không có QR (đọc ra None) thay vì sai id.
            proc.stdin.write(self._blank_png())
            proc.stdin.close()
        except BrokenPipeError:
            pass  # ffmpeg đã thoát sớm -> lỗi được báo qua returncode bên dưới
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if proc.wait() != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)

        print(f"  ✔ Hoàn thành: {output_video}")
        return str(output_video)

    def process_folder(self, input_folder, output_folder, use_gpu: Optional[bool | str] = None) -> dict:
        """Xử lý toàn bộ *.mp4 trong folder, output là <name>_qr.mp4."""
        os.makedirs(output_folder, exist_ok=True)
        video_files = sorted(Path(input_folder).glob("*.mp4"))

        if not video_files:
            print(f"⚠ Không tìm thấy file .mp4 nào trong {input_folder}")
            return {"success": 0, "failed": 0, "total": 0}

        print(f"📁 Tìm thấy {len(video_files)} video(s) để xử lý\n")
        success_count = 0
        failed_count = 0

        for idx, video_file in enumerate(video_files, 1):
            video_name = video_file.name
            video_out_name = f"{video_file.stem}_qr.mp4"
            output_video_path = os.path.join(output_folder, video_out_name)

            print(f"{'=' * 60}")
            print(f"[{idx}/{len(video_files)}] Đang xử lý: {video_out_name}")
            print(f"{'=' * 60}")

            try:
                self.process_video(str(video_file), output_video_path, use_gpu=use_gpu)
                success_count += 1
                print(f"✅ Thành công: {video_name}\n")
            except Exception as e:
                failed_count += 1
                print(f"❌ Lỗi khi xử lý {video_name}: {e}\n")

        print(f"\n{'=' * 60}")
        print("🎬 KẾT QUẢ XỬ LÝ")
        print(f"{'=' * 60}")
        print(f"✅ Thành công: {success_count}/{len(video_files)}")
        print(f"❌ Thất bại: {failed_count}/{len(video_files)}")
        print(f"📂 Thư mục output: {output_folder}")
        print(f"{'=' * 60}")
        return {"success": success_count, "failed": failed_count, "total": len(video_files)}

    # ---------------------------------------------------------------- helpers
    def _gpu(self, use_gpu: Optional[bool | str]) -> Optional[bool | str]:
        return self.use_gpu if use_gpu is None else use_gpu
