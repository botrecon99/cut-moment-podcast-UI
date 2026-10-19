LAUGHDETECTIONV2 - STRICT LOCAL V5.1 HOTFIX

V5.1 fixes Studio's false ModuleNotFoundError: studio_server and the
same sys.path issue affecting the AI model dependency check.

All Python runtimes, pip libraries, FFmpeg and model files are read from
THIS LaughDetectionV2 directory. No old parent directory is searched.

IMPORTANT: Do not delete _python311 or _python37 when upgrading from V5.
Just replace INSTALL_NEW_PC.bat and rerun it. Fully installed packages
are validated and skipped.

This ZIP contains only source and installer files. Keep your existing
Models/, audioset/, ui/ and video data folders untouched.

Tested via static checks and simulated Python import paths, not run on Windows.
