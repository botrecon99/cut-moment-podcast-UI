import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path


# ============================================================
# CẤU HÌNH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"
WORK_DIR = DOWNLOAD_DIR / "_cut_work"
DONE_DIR = BASE_DIR / "done"

LIST_FILE = BASE_DIR / "list.txt"
DONE_LINK_FILE = BASE_DIR / "doneLink.txt"

TEMP_VIDEO_NAME = "raw_video"
SOURCE_URL_FILE = DOWNLOAD_DIR / "raw_video_source.txt"
SOURCE_TITLE_FILE = DOWNLOAD_DIR / "raw_video_title.txt"

AUTO_UPDATE_YTDLP = True

# False: nếu video thô đúng link hiện tại thì dùng lại.
# True: luôn xóa video thô và tải lại.
FORCE_REDOWNLOAD = False

# True: xóa video thô sau khi xuất tất cả chapter thành công.
# False: giữ video thô cho tới khi code chuyển sang link tiếp theo.
DELETE_RAW_AFTER_DONE = True

# True: ưu tiên GPU NVIDIA. Nếu NVENC lỗi, tự chuyển sang CPU.
USE_NVENC = True

NVENC_CQ = "19"
X264_CRF = "18"
AUDIO_BITRATE = "192k"

# Bỏ qua phần cần giữ ngắn hơn giá trị này.
MIN_KEEP_SECONDS = 0.20

VIDEO_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".m4v"
)


# ============================================================
# KHỞI TẠO
# ============================================================

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)
DONE_DIR.mkdir(parents=True, exist_ok=True)


class UserQuit(Exception):
    """Người dùng yêu cầu dừng chương trình."""


def configure_console():
    if os.name == "nt":
        try:
            os.system("chcp 65001 > nul")
        except OSError:
            pass


# ============================================================
# TÌM CHƯƠNG TRÌNH
# ============================================================

def find_program(program_name):
    """Strict-local executable discovery; never load another tool install."""
    for directory in (BASE_DIR, BASE_DIR / 'tools' / 'ffmpeg' / 'bin',
                      BASE_DIR / '_python311' / 'Scripts'):
        executable = directory / program_name
        if executable.is_file():
            return str(executable)
    return None


YTDLP = find_program("yt-dlp.exe") or find_program("yt-dlp")
FFMPEG = find_program("ffmpeg.exe") or find_program("ffmpeg")
FFPROBE = find_program("ffprobe.exe") or find_program("ffprobe")


def check_required_programs():
    missing = []

    if not YTDLP:
        missing.append("yt-dlp")

    if not FFMPEG:
        missing.append("ffmpeg")

    if not FFPROBE:
        missing.append("ffprobe")

    if missing:
        print("\n❌ THIẾU CÁC CHƯƠNG TRÌNH SAU:")

        for item in missing:
            print(f"   - {item}")

        print("\nHãy đặt file .exe cùng thư mục code hoặc thêm vào PATH.")
        return False

    return True


# ============================================================
# QUẢN LÝ list.txt VÀ doneLink.txt
# ============================================================

def create_list_file():
    with open(LIST_FILE, "w", encoding="utf-8") as file:
        file.write("# Dán các link YouTube vào bên dưới, mỗi dòng một link\n")
        file.write("# https://www.youtube.com/watch?v=xxxxxxxxxxx\n")


def read_url_file(path):
    if not path.exists():
        return []

    urls = []
    seen = set()

    with open(path, "r", encoding="utf-8-sig") as file:
        for line in file:
            url = line.strip()

            if not url or url.startswith("#"):
                continue

            if url not in seen:
                seen.add(url)
                urls.append(url)

    return urls


def remove_url_from_list(video_url):
    if not LIST_FILE.exists():
        return

    original_lines = LIST_FILE.read_text(encoding="utf-8-sig").splitlines()
    remaining_lines = [
        line
        for line in original_lines
        if line.strip() != video_url
    ]

    temporary_file = LIST_FILE.with_suffix(".txt.tmp")
    content = "\n".join(remaining_lines).rstrip()

    if content:
        content += "\n"

    temporary_file.write_text(content, encoding="utf-8")
    os.replace(temporary_file, LIST_FILE)


def append_done_link(video_url):
    existing = set(read_url_file(DONE_LINK_FILE))

    if video_url in existing:
        return

    with open(DONE_LINK_FILE, "a", encoding="utf-8") as file:
        file.write(video_url + "\n")


def mark_link_done(video_url):
    """Chỉ gọi sau khi video đã xuất thành công."""

    append_done_link(video_url)
    remove_url_from_list(video_url)


def read_pending_urls():
    if not LIST_FILE.exists():
        create_list_file()
        print("\n📝 Đã tạo list.txt.")
        print("Hãy dán các link vào list.txt rồi chạy lại code.")

        if os.name == "nt":
            subprocess.run(["notepad.exe", str(LIST_FILE)])

        return []

    all_urls = read_url_file(LIST_FILE)

    if not all_urls:
        print("\n❌ list.txt không có link nào.")
        return []

    done_urls = set(read_url_file(DONE_LINK_FILE))
    already_done = [url for url in all_urls if url in done_urls]

    # Dọn các link đã có trong doneLink.txt khỏi list.txt.
    for url in already_done:
        remove_url_from_list(url)

    pending_urls = [url for url in all_urls if url not in done_urls]

    if already_done:
        print(
            f"\n⏭️ Đã bỏ {len(already_done)} link đã có trong doneLink.txt "
            "khỏi list.txt."
        )

    return pending_urls


# ============================================================
# JAVASCRIPT RUNTIME
# ============================================================

def get_javascript_arguments():
    deno = find_program("deno.exe") or find_program("deno")

    if deno:
        print("✅ JavaScript runtime: Deno")
        return [
            "--no-js-runtimes",
            "--js-runtimes",
            f"deno:{deno}"
        ]

    node = find_program("node.exe") or find_program("node")

    if node:
        print("✅ JavaScript runtime: Node.js")
        return [
            "--no-js-runtimes",
            "--js-runtimes",
            f"node:{node}"
        ]

    print("\n❌ Không tìm thấy Deno hoặc Node.js.")
    print("Hãy cài Deno hoặc Node.js, sau đó chạy lại code.")
    return None


# ============================================================
# CẬP NHẬT YT-DLP
# ============================================================

def update_ytdlp():
    if not AUTO_UPDATE_YTDLP:
        return

    print("\n🔄 Đang kiểm tra cập nhật yt-dlp...")

    result = subprocess.run([
        YTDLP,
        "--update-to",
        "nightly"
    ])

    if result.returncode != 0:
        print("⚠️ Không cập nhật được yt-dlp, tiếp tục dùng bản hiện tại.")


# ============================================================
# QUẢN LÝ VIDEO THÔ
# ============================================================

def find_raw_video():
    for extension in VIDEO_EXTENSIONS:
        video_path = DOWNLOAD_DIR / f"{TEMP_VIDEO_NAME}{extension}"

        if video_path.exists() and video_path.stat().st_size > 0:
            return video_path

    return None


def delete_old_raw_files():
    for item in DOWNLOAD_DIR.glob(f"{TEMP_VIDEO_NAME}.*"):
        try:
            if item.is_file():
                item.unlink()
        except OSError:
            pass

    SOURCE_URL_FILE.unlink(missing_ok=True)
    SOURCE_TITLE_FILE.unlink(missing_ok=True)


def can_reuse_existing_video(video_url):
    if FORCE_REDOWNLOAD:
        return False

    raw_video = find_raw_video()

    if not raw_video or not SOURCE_URL_FILE.exists():
        return False

    try:
        old_url = SOURCE_URL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False

    return old_url == video_url


# ============================================================
# TÊN FILE VÀ TIÊU ĐỀ VIDEO
# ============================================================

def clean_filename(title):
    title = str(title).strip()
    title = re.sub(r'[\\/:*?"<>|]', "", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" .-_")

    if not title:
        title = "video"

    reserved_names = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10))
    }

    if title.upper() in reserved_names:
        title = f"_{title}"

    return title[:180]


def read_saved_title():
    if not SOURCE_TITLE_FILE.exists():
        return None

    try:
        title = SOURCE_TITLE_FILE.read_text(encoding="utf-8").strip()
        return title or None
    except OSError:
        return None


def fetch_video_title(video_url, js_arguments, use_cookies=False):
    command = [
        YTDLP,
        "--ignore-config",
        "--no-plugin-dirs",
        *js_arguments,
        "--remote-components",
        "ejs:github",
        "--no-playlist",
        "--force-ipv4",
        "--quiet",
        "--no-warnings",
        "--skip-download",
        "--print",
        "%(title)s"
    ]

    cookie_file = BASE_DIR / "cookies.txt"

    if use_cookies and cookie_file.exists():
        command.extend(["--cookies", str(cookie_file)])

    command.append(video_url)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return None

    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    return clean_filename(lines[-1])


def get_video_title(video_url, js_arguments):
    if can_reuse_existing_video(video_url):
        saved_title = read_saved_title()

        if saved_title:
            return saved_title

    title = fetch_video_title(video_url, js_arguments, use_cookies=False)

    if not title and (BASE_DIR / "cookies.txt").exists():
        title = fetch_video_title(video_url, js_arguments, use_cookies=True)

    return title or "video"


# ============================================================
# TẢI VIDEO
# ============================================================

def build_download_command(video_url, js_arguments, use_cookies=False):
    output_template = str(DOWNLOAD_DIR / f"{TEMP_VIDEO_NAME}.%(ext)s")

    command = [
        YTDLP,
        "--ignore-config",
        "--no-plugin-dirs",
        *js_arguments,
        "--remote-components",
        "ejs:github",
        "--no-playlist",
        "--force-ipv4",
        "--geo-bypass",
        "--windows-filenames",
        "--force-overwrites",
        "--retries",
        "20",
        "--fragment-retries",
        "20",
        "--retry-sleep",
        "3",
        "-f",
        (
            "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]"
            "/bv*[height<=1080]+ba"
            "/b[height<=1080]"
            "/best"
        ),
        "--merge-output-format",
        "mp4",
        "-o",
        output_template
    ]

    cookie_file = BASE_DIR / "cookies.txt"

    if use_cookies and cookie_file.exists():
        command.extend(["--cookies", str(cookie_file)])

    command.append(video_url)
    return command


def run_download(video_url, js_arguments, use_cookies=False):
    command = build_download_command(
        video_url,
        js_arguments,
        use_cookies=use_cookies
    )

    if use_cookies:
        print("\n🍪 Đang thử tải bằng cookies.txt...")
    else:
        print("\n⬇️ Đang tải video YouTube...")

    result = subprocess.run(command)

    if result.returncode != 0:
        return None

    return find_raw_video()


def download_video(video_url, js_arguments, original_title):
    if can_reuse_existing_video(video_url):
        raw_video = find_raw_video()

        print("\n✅ Video này đã được tải trước đó.")
        print(f"📁 Dùng lại: {raw_video.name}")
        return raw_video

    delete_old_raw_files()

    raw_video = run_download(video_url, js_arguments, use_cookies=False)

    if not raw_video and (BASE_DIR / "cookies.txt").exists():
        delete_old_raw_files()
        raw_video = run_download(video_url, js_arguments, use_cookies=True)

    if not raw_video:
        print("\n❌ Không tải được video gốc.")
        print("Link này được giữ nguyên trong list.txt để xử lý lại sau.")
        return None

    try:
        SOURCE_URL_FILE.write_text(video_url, encoding="utf-8")
        SOURCE_TITLE_FILE.write_text(original_title, encoding="utf-8")
    except OSError:
        pass

    print("\n✅ Tải video thành công:")
    print(f"📁 {raw_video}")

    return raw_video


# ============================================================
# XỬ LÝ THỜI GIAN
# ============================================================

def timestamp_to_seconds(timestamp):
    parts = timestamp.strip().split(":")

    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None

    if len(numbers) == 2:
        minutes, seconds = numbers

        if seconds >= 60:
            return None

        return minutes * 60 + seconds

    if len(numbers) == 3:
        hours, minutes, seconds = numbers

        if minutes >= 60 or seconds >= 60:
            return None

        return hours * 3600 + minutes * 60 + seconds

    return None


def seconds_to_timestamp(total_seconds):
    total_seconds = max(0, int(round(total_seconds)))

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


# ============================================================
# DÁN TIMESTAMP TRỰC TIẾP TRONG CONSOLE
# ============================================================

def normalize_command_text(text):
    text = text.replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return text.upper().strip()


def parse_cut_ranges_from_lines(lines):
    # Hỗ trợ cả:
    # 00:00:00 --> 00:04:11
    # 01:34:29 --> END
    # END nghĩa là cắt từ mốc bắt đầu đến hết video.
    time_token = r"\d{1,4}:\d{2}(?::\d{2})?"
    end_token = rf"(?:{time_token}|END)"

    range_pattern = re.compile(
        rf"({time_token})\s*(?:-->|-|–|—|TO)\s*({end_token})",
        re.IGNORECASE
    )

    cut_ranges = []
    invalid_lines = []
    no_cut_requested = False

    for original_line in lines:
        line = original_line.strip()

        if not line or line.startswith("```"):
            continue

        normalized = normalize_command_text(line)

        if (
            "KHONG CO DOAN CAN CAT" in normalized
            or normalized in {"NONE", "NO CUT", "KEEP ALL", "GIU NGUYEN"}
        ):
            no_cut_requested = True
            continue

        match = range_pattern.search(line)

        if not match:
            # Bỏ qua các tiêu đề thường gặp thay vì coi là lỗi nghiêm trọng.
            if any(word in normalized for word in (
                "DOAN CAN CAT",
                "TONG KET",
                "INTRO",
                "OUTRO",
                "QUANG CAO"
            )):
                continue

            if line.startswith("#"):
                continue

            invalid_lines.append(line)
            continue

        start_text, end_text = match.groups()
        start_seconds = timestamp_to_seconds(start_text)

        # Dùng vô cực làm dấu hiệu "đến hết video".
        # normalize_cut_ranges() sẽ tự giới hạn về đúng thời lượng video.
        if normalize_command_text(end_text) == "END":
            end_seconds = float("inf")
        else:
            end_seconds = timestamp_to_seconds(end_text)

        if (
            start_seconds is None
            or end_seconds is None
            or end_seconds <= start_seconds
        ):
            invalid_lines.append(line)
            continue

        cut_ranges.append((float(start_seconds), float(end_seconds)))

    if no_cut_requested and cut_ranges:
        no_cut_requested = False

    return cut_ranges, no_cut_requested, invalid_lines


def read_cut_ranges_from_console(video_number, total_videos, title):
    while True:
        print("\n=====================================================")
        print(f"📝 VIDEO {video_number}/{total_videos}: {title}")
        print("Dán các khoảng CẦN CẮT BỎ trực tiếp vào cửa sổ này.")
        print("Mỗi dòng ví dụ: 00:00:00 --> 00:01:44")
        print("Có thể dùng: 01:34:29 --> END để cắt đến hết video.")
        print("")
        print("Sau khi dán xong, gõ DONE rồi nhấn Enter.")
        print("Gõ NONE nếu video không có đoạn nào cần cắt.")
        print("Gõ SKIP để bỏ qua link này.")
        print("Gõ QUIT để dừng chương trình.")
        print("=====================================================")

        pasted_lines = []

        while True:
            try:
                line = input()
            except EOFError:
                line = "DONE"

            command = normalize_command_text(line)

            if command in {"DONE", "XONG"}:
                break

            if command in {"SKIP", "BO QUA", "BOQUA"}:
                return "skip", [], False

            if command in {"QUIT", "EXIT", "THOAT"}:
                return "quit", [], False

            if command in {"NONE", "NO CUT", "KEEP ALL", "GIU NGUYEN"}:
                return "process", [], True

            # Dòng trống trong nội dung dán được bỏ qua; chỉ DONE mới kết thúc.
            if not line.strip():
                continue

            pasted_lines.append(line)

        cut_ranges, keep_all, invalid_lines = parse_cut_ranges_from_lines(
            pasted_lines
        )

        if invalid_lines:
            print("\n⚠️ Một số dòng không được nhận diện và đã bị bỏ qua:")
            for bad_line in invalid_lines[:10]:
                print(f"   - {bad_line}")

            if len(invalid_lines) > 10:
                print(f"   ... còn {len(invalid_lines) - 10} dòng khác")

        if cut_ranges or keep_all:
            return "process", cut_ranges, keep_all

        print("\n❌ Không tìm thấy khoảng thời gian hợp lệ.")
        print("Hãy dán lại, hoặc gõ NONE / SKIP / QUIT.")


# ============================================================
# DÁN CHAPTER / TÊN CLIP TRỰC TIẾP TRONG CONSOLE
# ============================================================

def parse_chapters_from_lines(lines):
    """Đọc chapter dạng: 00:13:20 | Tên câu chuyện."""

    time_token = r"\d{1,4}:\d{2}(?::\d{2})?"
    chapter_pattern = re.compile(
        rf"^\s*({time_token})\s*\|\s*(.+?)\s*$"
    )

    chapters = []
    invalid_lines = []
    use_original_title = False

    for original_line in lines:
        line = original_line.strip()

        if not line or line.startswith("```"):
            continue

        normalized = normalize_command_text(line)

        if normalized in {
            "NONE",
            "NO CHAPTER",
            "NO CHAPTERS",
            "USE ORIGINAL TITLE",
            "TEN VIDEO GOC",
            "GIU TEN GOC"
        }:
            use_original_title = True
            continue

        if any(word in normalized for word in (
            "CHAPTER YOUTUBE",
            "DANH SACH CHAPTER",
            "CHAPTER DAT TEN"
        )):
            continue

        if line.startswith("#"):
            continue

        match = chapter_pattern.match(line)

        if not match:
            invalid_lines.append(line)
            continue

        timestamp_text, title = match.groups()
        start_seconds = timestamp_to_seconds(timestamp_text)
        title = title.strip()

        if start_seconds is None or not title:
            invalid_lines.append(line)
            continue

        chapters.append((float(start_seconds), title))

    if chapters:
        use_original_title = False

    return chapters, use_original_title, invalid_lines


def read_chapters_from_console(video_number, total_videos, title):
    while True:
        print("\n=====================================================")
        print(f"🏷️ CHAPTER VIDEO {video_number}/{total_videos}: {title}")
        print("Dán danh sách chapter để CHIA và ĐẶT TÊN từng clip.")
        print("Mỗi dòng ví dụ:")
        print("00:00:41 | Tên câu chuyện thứ nhất")
        print("00:13:20 | Tên câu chuyện thứ hai")
        print("")
        print("Sau khi dán xong, gõ DONE rồi nhấn Enter.")
        print("Gõ NONE để xuất một clip và dùng tên video gốc.")
        print("Gõ SKIP để bỏ qua link này.")
        print("Gõ QUIT để dừng chương trình.")
        print("=====================================================")

        pasted_lines = []

        while True:
            try:
                line = input()
            except EOFError:
                line = "DONE"

            command = normalize_command_text(line)

            if command in {"DONE", "XONG"}:
                break

            if command in {"SKIP", "BO QUA", "BOQUA"}:
                return "skip", [], False

            if command in {"QUIT", "EXIT", "THOAT"}:
                return "quit", [], False

            if command in {
                "NONE",
                "NO CHAPTER",
                "NO CHAPTERS",
                "USE ORIGINAL TITLE",
                "TEN VIDEO GOC",
                "GIU TEN GOC"
            }:
                return "process", [], True

            if not line.strip():
                continue

            pasted_lines.append(line)

        chapters, use_original_title, invalid_lines = parse_chapters_from_lines(
            pasted_lines
        )

        if invalid_lines:
            print("\n⚠️ Một số dòng chapter không được nhận diện và đã bị bỏ qua:")
            for bad_line in invalid_lines[:10]:
                print(f"   - {bad_line}")

            if len(invalid_lines) > 10:
                print(f"   ... còn {len(invalid_lines) - 10} dòng khác")

        if chapters or use_original_title:
            return "process", chapters, use_original_title

        print("\n❌ Không tìm thấy chapter hợp lệ.")
        print("Hãy dán lại, hoặc gõ NONE / SKIP / QUIT.")


# ============================================================
# CHUẨN HÓA KHOẢNG CẮT VÀ TÍNH PHẦN GIỮ
# ============================================================

def normalize_cut_ranges(cut_ranges, video_duration):
    valid_ranges = []

    for start_seconds, end_seconds in cut_ranges:
        if start_seconds >= video_duration:
            continue

        start_seconds = max(0.0, start_seconds)
        end_seconds = min(float(video_duration), end_seconds)

        if end_seconds > start_seconds:
            valid_ranges.append((start_seconds, end_seconds))

    valid_ranges.sort(key=lambda item: item[0])
    merged = []

    for start_seconds, end_seconds in valid_ranges:
        if not merged or start_seconds > merged[-1][1] + 0.05:
            merged.append([start_seconds, end_seconds])
        else:
            merged[-1][1] = max(merged[-1][1], end_seconds)

    return [(start, end) for start, end in merged]


def build_keep_ranges(cut_ranges, video_duration):
    keep_ranges = []
    cursor = 0.0

    for cut_start, cut_end in cut_ranges:
        if cut_start - cursor >= MIN_KEEP_SECONDS:
            keep_ranges.append((cursor, cut_start))

        cursor = max(cursor, cut_end)

    if video_duration - cursor >= MIN_KEEP_SECONDS:
        keep_ranges.append((cursor, float(video_duration)))

    return keep_ranges



def normalize_chapters(chapters, video_duration):
    """Sắp xếp chapter, loại mốc ngoài video và mốc trùng nhau."""

    valid = []

    for start_seconds, title in chapters:
        if start_seconds < 0 or start_seconds >= video_duration:
            continue

        safe_title = str(title).strip()

        if not safe_title:
            continue

        valid.append((float(start_seconds), safe_title))

    valid.sort(key=lambda item: item[0])

    # Nếu có nhiều tiêu đề cùng một mốc, giữ dòng cuối cùng.
    deduplicated = []

    for start_seconds, title in valid:
        if deduplicated and abs(start_seconds - deduplicated[-1][0]) <= 0.001:
            deduplicated[-1] = (start_seconds, title)
        else:
            deduplicated.append((start_seconds, title))

    return deduplicated


def build_clip_specs(
    keep_ranges,
    chapters,
    video_duration,
    original_title,
    use_original_title=False
):
    """
    Mỗi chapter luôn tương ứng đúng MỘT file đầu ra.

    Nếu một hoặc nhiều khoảng cắt nằm giữa chapter, chapter đó sẽ chứa
    nhiều source_ranges. Các source_ranges này được render riêng rồi nối
    lại thành một file duy nhất ở bước xuất video.
    """

    if use_original_title or not chapters:
        normalized_chapters = [(0.0, original_title)]
    else:
        normalized_chapters = normalize_chapters(chapters, video_duration)

    if not normalized_chapters:
        return []

    chapter_ranges = []

    for index, (chapter_start, chapter_title) in enumerate(
        normalized_chapters
    ):
        if index + 1 < len(normalized_chapters):
            chapter_end = normalized_chapters[index + 1][0]
        else:
            chapter_end = float(video_duration)

        if chapter_end - chapter_start < MIN_KEEP_SECONDS:
            continue

        chapter_ranges.append({
            "chapter_index": index + 1,
            "title": chapter_title,
            "chapter_start": chapter_start,
            "chapter_end": chapter_end
        })

    clip_specs = []

    for chapter in chapter_ranges:
        source_ranges = []

        for keep_start, keep_end in keep_ranges:
            segment_start = max(chapter["chapter_start"], keep_start)
            segment_end = min(chapter["chapter_end"], keep_end)

            if segment_end - segment_start >= MIN_KEEP_SECONDS:
                source_ranges.append((segment_start, segment_end))

        # Chapter bị cắt bỏ hoàn toàn thì không tạo file rỗng.
        if not source_ranges:
            continue

        clip_specs.append({
            "chapter_index": chapter["chapter_index"],
            "title": chapter["title"],
            "chapter_start": chapter["chapter_start"],
            "chapter_end": chapter["chapter_end"],
            "source_ranges": source_ranges,
            "start": source_ranges[0][0],
            "end": source_ranges[-1][1],
            "kept_duration": sum(
                end_seconds - start_seconds
                for start_seconds, end_seconds in source_ranges
            )
        })

    for global_index, spec in enumerate(clip_specs, start=1):
        spec["global_index"] = global_index

    return clip_specs


# ============================================================
# THÔNG TIN VIDEO
# ============================================================

def get_video_duration(video_path):
    command = [
        FFPROBE,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return None

    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


# ============================================================
# RENDER CÁC PHẦN CẦN GIỮ
# ============================================================

def clear_work_dir():
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    WORK_DIR.mkdir(parents=True, exist_ok=True)


def build_video_encoding_args(use_nvenc):
    if use_nvenc:
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-rc", "vbr",
            "-cq", NVENC_CQ,
            "-b:v", "0"
        ]

    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", X264_CRF
    ]


def render_keep_segment(
    raw_video,
    start_seconds,
    end_seconds,
    output_file,
    use_nvenc
):
    duration = end_seconds - start_seconds

    command = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{start_seconds:.3f}",
        "-i", str(raw_video),
        "-t", f"{duration:.3f}",
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-fflags", "+genpts",
        *build_video_encoding_args(use_nvenc),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", "48000",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        str(output_file)
    ]

    result = subprocess.run(command)

    return (
        result.returncode == 0
        and output_file.exists()
        and output_file.stat().st_size > 0
    )


def _escape_ffconcat_path(path):
    """Escape đường dẫn cho file danh sách của FFmpeg concat demuxer."""

    path_text = str(Path(path).resolve()).replace("\\", "/")
    return path_text.replace("'", "'\\''")


def write_concat_list(segment_files, concat_list_file):
    with open(concat_list_file, "w", encoding="utf-8", newline="\n") as file:
        for segment_file in segment_files:
            escaped_path = _escape_ffconcat_path(segment_file)
            file.write(f"file '{escaped_path}'\n")


def concat_rendered_segments(
    segment_files,
    output_file,
    use_nvenc
):
    """
    Nối các đoạn đã render của cùng một chapter.

    Thử stream-copy trước vì tất cả đoạn đều được encode cùng cấu hình.
    Nếu stream-copy lỗi, tự nối và encode lại.
    """

    if not segment_files:
        return False

    if len(segment_files) == 1:
        output_file.unlink(missing_ok=True)
        shutil.move(str(segment_files[0]), str(output_file))
        return output_file.exists() and output_file.stat().st_size > 0

    concat_list_file = output_file.with_suffix(".ffconcat.txt")
    write_concat_list(segment_files, concat_list_file)

    copy_command = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c", "copy",
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(output_file)
    ]

    result = subprocess.run(copy_command)

    if (
        result.returncode == 0
        and output_file.exists()
        and output_file.stat().st_size > 0
    ):
        concat_list_file.unlink(missing_ok=True)
        return True

    # Stream-copy lỗi: nối lại bằng encode để tăng độ tương thích.
    output_file.unlink(missing_ok=True)

    encode_command = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-fflags", "+genpts",
        *build_video_encoding_args(use_nvenc),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", "48000",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        str(output_file)
    ]

    result = subprocess.run(encode_command)
    concat_list_file.unlink(missing_ok=True)

    return (
        result.returncode == 0
        and output_file.exists()
        and output_file.stat().st_size > 0
    )


def render_chapter_clips_mode(raw_video, clip_specs, use_nvenc):
    """Render toàn bộ chapter bằng NVENC hoặc CPU."""

    rendered_clip_files = []
    total_clips = len(clip_specs)

    for clip_position, spec in enumerate(clip_specs, start=1):
        source_ranges = spec["source_ranges"]
        chapter_work_dir = WORK_DIR / f"chapter_{clip_position:04d}"
        chapter_work_dir.mkdir(parents=True, exist_ok=True)

        print(
            f'🎬 [{clip_position}/{total_clips}] Chapter: '
            f'{spec["title"]}'
        )

        rendered_parts = []

        for part_position, (start_seconds, end_seconds) in enumerate(
            source_ranges,
            start=1
        ):
            part_file = chapter_work_dir / f"part_{part_position:04d}.mp4"

            print(
                f"   🎞️ Đoạn {part_position}/{len(source_ranges)}: "
                f"{seconds_to_timestamp(start_seconds)} → "
                f"{seconds_to_timestamp(end_seconds)}"
            )

            success = render_keep_segment(
                raw_video,
                start_seconds,
                end_seconds,
                part_file,
                use_nvenc=use_nvenc
            )

            if not success:
                print(
                    f"   ❌ Render thất bại ở chapter "
                    f"{clip_position}, đoạn {part_position}."
                )
                return None

            rendered_parts.append(part_file)

        chapter_output = WORK_DIR / f"clip_{clip_position:04d}.mp4"

        if len(rendered_parts) > 1:
            print(
                f"   🔗 Đang nối {len(rendered_parts)} đoạn "
                "thành một clip chapter..."
            )

        success = concat_rendered_segments(
            rendered_parts,
            chapter_output,
            use_nvenc=use_nvenc
        )

        if not success:
            print(f"   ❌ Không nối được chapter {clip_position}.")
            return None

        rendered_clip_files.append(chapter_output)

    return rendered_clip_files


def render_all_chapter_clips(raw_video, clip_specs):
    """
    Mỗi spec tạo đúng một file.
    Nếu NVENC lỗi ở bất kỳ bước nào, xóa kết quả tạm và chạy lại bằng CPU.
    """

    if USE_NVENC:
        rendered_files = render_chapter_clips_mode(
            raw_video,
            clip_specs,
            use_nvenc=True
        )

        if rendered_files:
            return rendered_files

        print("\n⚠️ NVENC lỗi, chuyển toàn bộ quá trình sang CPU libx264...")
        clear_work_dir()

    return render_chapter_clips_mode(
        raw_video,
        clip_specs,
        use_nvenc=False
    )


# ============================================================
# XUẤT CÁC CLIP RIÊNG BIỆT
# ============================================================

def create_clips_output_dir(original_title):
    """Tạo thư mục riêng cho các clip của một video."""

    folder_name = f"{clean_filename(original_title)} - clips"
    output_dir = DONE_DIR / folder_name

    # Xóa kết quả cũ cùng tên để tránh sót clip từ lần chạy trước.
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def build_clip_filename(spec):
    """Mỗi chapter có đúng một file, không thêm Part do khoảng cắt giữa."""

    safe_title = clean_filename(spec["title"])
    prefix = f'{spec["global_index"]:02d}'
    return f"{prefix} - {safe_title}.mp4"


def move_segments_to_done(rendered_clip_files, clip_specs, original_title):
    """Chuyển mỗi file chapter hoàn chỉnh sang done và đặt tên."""

    if (
        not rendered_clip_files
        or len(rendered_clip_files) != len(clip_specs)
    ):
        return None

    output_dir = create_clips_output_dir(original_title)
    final_paths = []

    try:
        for rendered_file, spec in zip(rendered_clip_files, clip_specs):
            final_name = build_clip_filename(spec)
            final_path = output_dir / final_name

            if final_path.exists():
                final_path.unlink()

            shutil.move(str(rendered_file), str(final_path))

            if not final_path.exists() or final_path.stat().st_size <= 0:
                raise OSError(f"File clip không hợp lệ: {final_name}")

            final_paths.append(final_path)

        info_file = output_dir / "clips_timestamps.txt"

        with open(info_file, "w", encoding="utf-8") as file:
            for spec, final_path in zip(clip_specs, final_paths):
                file.write(f'{final_path.name}\n')
                file.write(f'  Title: {spec["title"]}\n')
                file.write('  Source ranges:\n')

                for start_seconds, end_seconds in spec["source_ranges"]:
                    file.write(
                        f'    {seconds_to_timestamp(start_seconds)} --> '
                        f'{seconds_to_timestamp(end_seconds)}\n'
                    )

                file.write(
                    f'  Kept duration: '
                    f'{seconds_to_timestamp(spec["kept_duration"])}\n\n'
                )

    except OSError as error:
        print(f"\n❌ Lỗi khi lưu clip: {error}")
        shutil.rmtree(output_dir, ignore_errors=True)
        return None

    return final_paths


# ============================================================
# XỬ LÝ MỘT VIDEO
# ============================================================

def process_video(
    raw_video,
    original_title,
    cut_ranges,
    chapters,
    keep_all=False,
    use_original_title=False
):
    video_duration = get_video_duration(raw_video)

    if video_duration is None:
        print("\n❌ Không đọc được thời lượng video.")
        return None

    if keep_all:
        normalized_cuts = []
        keep_ranges = [(0.0, float(video_duration))]
    else:
        normalized_cuts = normalize_cut_ranges(
            cut_ranges,
            video_duration
        )

        if not normalized_cuts:
            print("\n❌ Không có khoảng cắt nào nằm trong thời lượng video.")
            return None

        keep_ranges = build_keep_ranges(
            normalized_cuts,
            video_duration
        )

    if not keep_ranges:
        print("\n❌ Các khoảng cắt đã bao phủ toàn bộ video.")
        return None

    clip_specs = build_clip_specs(
        keep_ranges,
        chapters,
        video_duration,
        original_title,
        use_original_title=use_original_title
    )

    if not clip_specs:
        print("\n❌ Không tạo được clip nào từ chapter và mốc cắt.")
        print("Kiểm tra xem chapter có nằm trong thời lượng video hay không.")
        return None

    print("\n=====================================================")
    print(f"🎬 Video gốc: {original_title}")
    print(f"🕒 Thời lượng: {seconds_to_timestamp(video_duration)}")

    if keep_all:
        print("✂️ Không có phần thừa cần cắt")
    else:
        print(f"✂️ Số khoảng cần bỏ: {len(normalized_cuts)}")

    print(f"🏷️ Số chapter nhập: {len(chapters)}")
    print(f"✅ Số clip sẽ xuất: {len(clip_specs)}")
    print("=====================================================\n")

    if normalized_cuts:
        print("CÁC KHOẢNG SẼ CẮT BỎ:")

        for start_seconds, end_seconds in normalized_cuts:
            print(
                f"   ❌ {seconds_to_timestamp(start_seconds)} --> "
                f"{seconds_to_timestamp(end_seconds)}"
            )

        print("")

    print("CÁC CLIP SẼ ĐƯỢC XUẤT RIÊNG:")

    for spec in clip_specs:
        print(
            f'   ✅ Clip {spec["global_index"]:02d}: '
            f'{spec["title"]}'
        )

        for range_index, (start_seconds, end_seconds) in enumerate(
            spec["source_ranges"],
            start=1
        ):
            print(
                f'      Nguồn {range_index:02d}: '
                f'{seconds_to_timestamp(start_seconds)} --> '
                f'{seconds_to_timestamp(end_seconds)}'
            )

        if len(spec["source_ranges"]) > 1:
            print(
                f'      🔗 {len(spec["source_ranges"])} đoạn trên sẽ '
                'được nối thành MỘT file'
            )

    print("")
    clear_work_dir()

    rendered_clip_files = render_all_chapter_clips(
        raw_video,
        clip_specs
    )

    if not rendered_clip_files:
        print("\n❌ Không render/nối được các clip chapter.")
        return None

    print("\n📦 Đang đặt tên theo chapter và chuyển vào thư mục done...")

    final_paths = move_segments_to_done(
        rendered_clip_files,
        clip_specs,
        original_title
    )

    if not final_paths:
        print("\n❌ Không chuyển được các clip vào thư mục done.")
        return None

    output_dir = final_paths[0].parent

    print("\n=====================================================")
    print("✅ ĐÃ XUẤT MỖI CHAPTER THÀNH MỘT CLIP")
    print(f"📁 Thư mục kết quả: {output_dir}")
    print(f"🎞️ Tổng số clip: {len(final_paths)}")

    for index, final_path in enumerate(final_paths, start=1):
        clip_duration = get_video_duration(final_path)

        if clip_duration is None:
            print(f"   Clip {index:02d}: {final_path.name}")
        else:
            print(
                f"   Clip {index:02d}: {final_path.name} "
                f"({seconds_to_timestamp(clip_duration)})"
            )

    print("=====================================================")

    clear_work_dir()
    return final_paths


# ============================================================
# DỌN FILE SAU MỖI LINK
# ============================================================

def cleanup_after_success(raw_video):
    clear_work_dir()

    if DELETE_RAW_AFTER_DONE:
        try:
            raw_video.unlink(missing_ok=True)
            SOURCE_URL_FILE.unlink(missing_ok=True)
            SOURCE_TITLE_FILE.unlink(missing_ok=True)
            print("🗑️ Đã xóa video thô của link vừa xử lý.")
        except OSError as error:
            print(f"⚠️ Không xóa được video thô: {error}")


# ============================================================
# XỬ LÝ HÀNG LOẠT
# ============================================================

def process_all_links(pending_urls, js_arguments):
    total = len(pending_urls)
    success_count = 0
    failed_count = 0
    skipped_count = 0

    for index, video_url in enumerate(pending_urls, start=1):
        print("\n\n#####################################################")
        print(f"🔄 LINK {index}/{total}")
        print(video_url)
        print("#####################################################")

        original_title = get_video_title(video_url, js_arguments)
        print(f"\n🏷️ Tiêu đề video gốc: {original_title}")

        raw_video = download_video(
            video_url,
            js_arguments,
            original_title
        )

        if not raw_video:
            failed_count += 1
            print("⏭️ Tự chuyển sang link tiếp theo.")
            continue

        status, cut_ranges, keep_all = read_cut_ranges_from_console(
            index,
            total,
            original_title
        )

        if status == "quit":
            raise UserQuit()

        if status == "skip":
            skipped_count += 1
            print("\n⏭️ Đã bỏ qua link này; link vẫn nằm trong list.txt.")
            continue

        chapter_status, chapters, use_original_title = read_chapters_from_console(
            index,
            total,
            original_title
        )

        if chapter_status == "quit":
            raise UserQuit()

        if chapter_status == "skip":
            skipped_count += 1
            print("\n⏭️ Đã bỏ qua link này; link vẫn nằm trong list.txt.")
            continue

        final_paths = process_video(
            raw_video,
            original_title,
            cut_ranges,
            chapters,
            keep_all=keep_all,
            use_original_title=use_original_title
        )

        if not final_paths:
            failed_count += 1
            print("\n❌ Link chưa hoàn tất nên vẫn được giữ trong list.txt.")
            print("⏭️ Tự chuyển sang link tiếp theo.")
            continue

        try:
            mark_link_done(video_url)
            print("✅ Đã chuyển link từ list.txt sang doneLink.txt.")
        except OSError as error:
            print(f"⚠️ Video đã xuất xong nhưng chưa cập nhật được link: {error}")

        cleanup_after_success(raw_video)
        success_count += 1

        if index < total:
            print("\n➡️ Đang chuyển sang link tiếp theo...")

    return success_count, failed_count, skipped_count


# ============================================================
# CHẠY CHƯƠNG TRÌNH
# ============================================================

def main():
    configure_console()

    print("=====================================================")
    print(" YOUTUBE STORY-ONLY BATCH SPLITTER")
    print(" DÁN MỐC CẮT + CHAPTER + MỖI CHAPTER MỘT CLIP")
    print("=====================================================")

    if not check_required_programs():
        return

    pending_urls = read_pending_urls()

    if not pending_urls:
        print("\n✅ Không còn link nào cần xử lý.")
        return

    js_arguments = get_javascript_arguments()

    if not js_arguments:
        return

    update_ytdlp()

    print(f"\n📋 Có {len(pending_urls)} link đang chờ xử lý.")

    success_count, failed_count, skipped_count = process_all_links(
        pending_urls,
        js_arguments
    )

    print("\n\n=====================================================")
    print("📊 KẾT QUẢ TOÀN BỘ DANH SÁCH")
    print(f"✅ Thành công: {success_count}")
    print(f"❌ Thất bại: {failed_count}")
    print(f"⏭️ Bỏ qua: {skipped_count}")
    print(f"📁 Các clip đã tách: {DONE_DIR}")
    print(f"📄 Link hoàn thành: {DONE_LINK_FILE}")
    print("=====================================================")

    if success_count > 0 and os.name == "nt":
        os.startfile(DONE_DIR)


if __name__ == "__main__":
    try:
        main()

    except UserQuit:
        print("\n\n⛔ Đã dừng theo yêu cầu. Link chưa xong vẫn ở list.txt.")

    except KeyboardInterrupt:
        print("\n\n⛔ Bạn đã dừng chương trình bằng Ctrl+C.")

    except Exception as error:
        print("\n❌ Chương trình gặp lỗi:")
        print(error)

    input("\nNhấn Enter để đóng chương trình...")