LAUGH DETECTION V2 — UI V3.7.3 (MERGE / UNMERGE CLIPS)
=====================================================
Nâng cấp từ V3.7.2, giữ nguyên thiết kế timeline VIDEO + CLIP, sóng âm,
hover preview khi Pause, kéo START/END, snapping, phụ đề, detect và export.

QUAN TRỌNG — PHẢI CHÉP CẢ HAI FILE:
  1) studio_server.py    (backend lưu lịch sử ghép)
  2) ui/index.html       (giao diện phím tắt / chọn clip / nhãn)

CÀI ĐẶT
  1. TẮT cửa sổ START_STUDIO.bat và trình duyệt đang mở Studio.
  2. Sao lưu 2 file studio_server.py và ui/index.html trong LaughDetectionV2.
  3. Giải nén ZIP vào thư mục LaughDetectionV2, cho phép GHI ĐÈ cả hai.
  4. Mở START_STUDIO.bat; trong trình duyệt nhấn Ctrl+Shift+R.
  5. Nhìn nhãn góc trái trên: V3.7.3.
KHÔNG chạy lại INSTALL, KHÔNG tải lại video, KHÔNG sửa AI / FFmpeg.

MERGE — Ctrl + Click (2 clip) -> Alt + G
  * Có thể Ctrl+Click clip ở timeline hoặc tên clip trong bảng.
  * Chọn CHÍNH XÁC 2 clip, mỗi clip chỉ có 1 source segment.
  * END clip bên trái PHẢI BẰNG START clip bên phải (đến mili-giây).
  * Không tự bỏ khoảng trống, không đẩy hai clip vào nhau, không ghép khi chồng lấn.
  * Ghép 04 + 05 sẽ thành MERGED CLIP 04–05, có biểu tượng xích.
  * Ghép tiếp clip 06 sẽ thành MERGED CLIP 04–06.
  * Nếu 2 clip chưa được duyệt hết, clip ghép sẽ CHƯA DUYỆT để tránh xuất nhầm.
  * Render chỉ xem clip ghép như MỘT đoạn liên tục.

UNMERGE — Click clip đã ghép -> Alt + G
  * Khôi phục đủ clip gốc, tên/title/tag, trạng thái duyệt và mốc START/END gốc.
  * Nếu mốc gốc hiện trùng với clip khác đã chỉnh sau đó, Studio sẽ không tách
    để tránh tạo chồng lấn. Chỉnh xung đột rồi Alt+G lại.
  * Lịch sử ghép được lưu vào review_plan.json trên máy (server V3.7.3).
  * Có thể đóng rồi mở lại Studio và vẫn tách được.
  * Nếu merge 04+05 rồi ghép thêm 06, Alt+G tách về 04, 05, 06 gốc.

GHI CHÚ
  * Đây không phải nối lại video nguồn hoặc render tức thời.
  * Phần ghi file xuất vẫn sử dụng quy trình render khi bấm XUẤT.
  * Một clip gốc có nhiều source segments tách rời không tham gia ghép liền mạch.
  * Giới hạn an toàn: tối đa 200 clip gốc trong một clip ghép.
  * Khi kéo mép clip đã ghép, mở lại và Alt+G vẫn trả đúng mốc gốc trước khi ghép.
