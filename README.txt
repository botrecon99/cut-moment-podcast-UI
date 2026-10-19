LaughDetectionV2 - portable installer with older FFmpeg behavior

Copy INSTALL_NEW_PC.bat into the existing LaughDetectionV2 directory. Run it.
Existing local Python 3.7 / 3.11 installations and AI libraries are reused.
FFmpeg is NOT downloaded: existing installed FFmpeg is copied into
LaughDetectionV2\tools\ffmpeg\bin. If FFmpeg is not installed anywhere,
copy ffmpeg.exe and ffprobe.exe (plus any required DLLs) into that folder
from a trusted FFmpeg installation, then run INSTALL again.

This package does not replace project source files, models or video data.
The installer still needs internet to install Python or libraries if missing.
