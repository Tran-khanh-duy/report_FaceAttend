"""
services/report_service.py
Xuất báo cáo điểm danh — Excel + PDF.
"""
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from loguru import logger

from core.config import app_config, report_config


# ─────────────────────────────────────────────
@dataclass
class ReportData:
    session_id:     int
    class_code:     str
    class_name:     str
    subject_name:   str
    session_date:   str
    start_time:     str
    end_time:       str
    teacher_name:   str
    total_students: int
    present_count:  int
    absent_count:   int
    late_count:     int = 0
    records:        list = None

    @property
    def attendance_rate(self) -> float:
        if self.total_students == 0:
            return 0.0
        return self.present_count / self.total_students * 100

    @property
    def title(self) -> str:
        return f"BẢNG ĐIỂM DANH — {self.subject_name}"


# ─────────────────────────────────────────────
def load_report_data(session_id: int) -> Optional[ReportData]:
    """Load dữ liệu điểm danh 1 buổi từ DB."""
    try:
        from database.repositories import session_repo, record_repo, class_repo

        session = session_repo.get_by_id(session_id)
        if not session:
            logger.error(f"Không tìm thấy session {session_id}")
            return None

        records = record_repo.get_session_report(session_id)
        present = [r for r in records if r.get("status") == "PRESENT"]
        absent  = [r for r in records if r.get("status") == "ABSENT"]

        # Lấy teacher_name từ Classes — session không join trực tiếp
        teacher_name = ""
        cls = class_repo.get_by_id(session.class_id)
        if cls:
            teacher_name = cls.teacher_name or ""

        data = ReportData(
            session_id=session_id,
            class_code=getattr(session, "class_code", "") or "",
            class_name=getattr(session, "class_name", "") or "",
            subject_name=session.subject_name,
            session_date=(
                session.session_date.strftime("%d/%m/%Y")
                if session.session_date else ""
            ),
            start_time=(
                session.start_time.strftime("%H:%M:%S")
                if session.start_time else "—"
            ),
            end_time=(
                session.end_time.strftime("%H:%M:%S")
                if session.end_time else "—"
            ),
            teacher_name=teacher_name,
            # total_students = len(records) — không dùng session.total_students
            total_students=len(records),
            present_count=len(present),
            absent_count=len(absent),
            records=records,
        )
        return data

    except Exception as e:
        logger.error(f"load_report_data error: {e}")
        return None


# ─────────────────────────────────────────────
#  Excel Report
# ─────────────────────────────────────────────
def export_excel(data: ReportData, output_path: str = None) -> Optional[str]:
    """Xuất Excel theo theme hiện đại: nền trắng, header xanh đậm, rows màu theo trạng thái."""
    try:
        import pandas as pd
        from openpyxl import Workbook
        from openpyxl.styles import (Font, PatternFill, Alignment, Border, Side,
                                      GradientFill)
        from openpyxl.utils import get_column_letter
        from openpyxl.formatting.rule import ColorScaleRule

        records = data.records or []
        df = pd.DataFrame(records) if records else pd.DataFrame(
            columns=["student_code","full_name","class_name","gender",
                     "status","check_in_time","recognition_score","building"])
        for col in ["building","class_name","status","gender","recognition_score"]:
            if col not in df.columns:
                df[col] = ""

        # ── Màu sắc (theme sáng, in được) ──────────────────────────────
        C_HDR_BG   = "1E3A5F"   # Xanh đậm Navy — header chính
        C_HDR_FG   = "FFFFFF"   # Trắng
        C_SUB_BG   = "2E6DA4"   # Xanh dương nhạt — subheader
        C_SUB_FG   = "FFFFFF"
        C_PRESENT  = "E8F5E9"   # Xanh lá cực nhạt
        C_ABSENT   = "FFF3E0"   # Cam nhạt
        C_ALT      = "F5F9FF"   # Xanh nhạt alternating
        C_WHITE    = "FFFFFF"
        C_BORDER   = "C5D4E8"   # Xanh nhạt
        C_GREEN    = "2E7D32"
        C_RED      = "C62828"
        C_SCORE    = "1565C0"

        def cell_border(thin=True):
            s = "thin" if thin else "hair"
            c = C_BORDER
            return Border(left=Side(style=s,color=c), right=Side(style=s,color=c),
                          top=Side(style=s,color=c), bottom=Side(style=s,color=c))

        def hdr_fill(color): return PatternFill("solid", fgColor=color)
        def row_fill(color): return PatternFill("solid", fgColor=color)

        wb = Workbook()

        # ═══════════════════════════════════════════════════════════════
        # SHEET 1 — TỔNG HỢP
        # ═══════════════════════════════════════════════════════════════
        ws = wb.active
        ws.title = "Tổng hợp"
        ws.sheet_view.showGridLines = False

        # Banner tiêu đề
        ws.merge_cells("A1:G1")
        c = ws["A1"]
        c.value = "HỆ THỐNG ĐIỂM DANH KHUÔN MẶT — BÁO CÁO TỔNG HỢP"
        c.font = Font(name="Calibri", size=16, bold=True, color=C_HDR_FG)
        c.fill = hdr_fill(C_HDR_BG)
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 40

        # Thông tin buổi học
        info = [
            ("Môn học:", data.subject_name),
            ("Lớp học:", f"{data.class_code} — {data.class_name}"),
            ("Ngày:", data.session_date),
            ("Giờ:", f"{data.start_time} — {data.end_time}"),
            ("Giáo viên:", data.teacher_name or "—"),
        ]
        for i, (label, val) in enumerate(info, 2):
            lc = ws.cell(row=i, column=1, value=label)
            lc.font = Font(name="Calibri", bold=True, color="555555", size=10)
            lc.fill = row_fill("EEF4FB")
            vc = ws.cell(row=i, column=2, value=val)
            vc.font = Font(name="Calibri", size=10)
            vc.fill = row_fill(C_WHITE)
            ws.merge_cells(f"B{i}:G{i}")
            ws.row_dimensions[i].height = 18

        # Thống kê tổng quan
        row_stat = len(info) + 3
        ws.merge_cells(f"A{row_stat}:G{row_stat}")
        h = ws.cell(row=row_stat, column=1, value="THỐNG KÊ NHANH")
        h.font = Font(name="Calibri", size=11, bold=True, color=C_HDR_FG)
        h.fill = hdr_fill(C_SUB_BG)
        h.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[row_stat].height = 24

        stat_labels = ["Tổng học viên","Có mặt","Vắng mặt","Tỉ lệ (%)"]
        stat_vals   = [data.total_students, data.present_count,
                       data.absent_count, f"{data.attendance_rate:.1f}%"]
        stat_colors = [C_HDR_BG, C_GREEN, C_RED, "7B1FA2"]

        for ci, (lb, vl, cl) in enumerate(zip(stat_labels, stat_vals, stat_colors), 1):
            r1 = row_stat + 1
            ws.cell(row=r1, column=ci, value=lb).font = Font(name="Calibri", bold=True, color=C_HDR_FG, size=9)
            ws.cell(row=r1, column=ci).fill = hdr_fill(cl)
            ws.cell(row=r1, column=ci).alignment = Alignment(horizontal="center")
            ws.row_dimensions[r1].height = 20
            r2 = row_stat + 2
            ws.cell(row=r2, column=ci, value=vl).font = Font(name="Calibri", bold=True, color=cl, size=18)
            ws.cell(row=r2, column=ci).fill = row_fill(C_WHITE)
            ws.cell(row=r2, column=ci).alignment = Alignment(horizontal="center")
            ws.row_dimensions[r2].height = 32

        # Bảng tổng hợp theo tòa nhà
        row_tbl = row_stat + 5
        if not df.empty and "building" in df.columns:
            df_bld = df.groupby("building").agg(
                Tổng=("student_code","count"),
                Có_mặt=("status", lambda x: (x=="PRESENT").sum()),
                Vắng=("status", lambda x: (x=="ABSENT").sum())
            ).reset_index()
            df_bld["Tỉ_lệ_%"] = (df_bld["Có_mặt"]/df_bld["Tổng"]*100).round(1)

            ws.merge_cells(f"A{row_tbl}:E{row_tbl}")
            th = ws.cell(row=row_tbl, column=1, value="THỐNG KÊ THEO TÒA NHÀ")
            th.font = Font(name="Calibri", bold=True, color=C_HDR_FG, size=11)
            th.fill = hdr_fill(C_HDR_BG)
            th.alignment = Alignment(horizontal="center")
            ws.row_dimensions[row_tbl].height = 22

            col_hdrs = ["Tòa nhà","Tổng SV","Có mặt","Vắng mặt","Tỉ lệ (%)"]
            for ci, h in enumerate(col_hdrs, 1):
                c = ws.cell(row=row_tbl+1, column=ci, value=h)
                c.font = Font(name="Calibri", bold=True, color=C_HDR_FG, size=10)
                c.fill = hdr_fill(C_SUB_BG)
                c.alignment = Alignment(horizontal="center")
                c.border = cell_border()

            for ri, row_d in enumerate(df_bld.itertuples(index=False), row_tbl+2):
                for ci, val in enumerate(row_d, 1):
                    c = ws.cell(row=ri, column=ci, value=val)
                    c.fill = row_fill(C_ALT if ri%2==0 else C_WHITE)
                    c.alignment = Alignment(horizontal="center")
                    c.border = cell_border()
                    c.font = Font(name="Calibri", size=10)

        for ci, w in enumerate([20,14,12,12,12], 1):
            ws.column_dimensions[get_column_letter(ci)].width = w
        ws.column_dimensions["B"].width = 30

        # ═══════════════════════════════════════════════════════════════
        # SHEET 2+ — MỘT SHEET PER LỚP với đầy đủ chi tiết
        # ═══════════════════════════════════════════════════════════════
        if df.empty:
            ws2 = wb.create_sheet("Không có dữ liệu")
            ws2["A1"] = "Không có dữ liệu điểm danh cho buổi này."
        else:
            grouped = df.groupby("class_name")
            for c_name, grp in grouped:
                sname = str(c_name)[:31]
                for ch in r':\/?*[]': sname = sname.replace(ch, "")
                ws2 = wb.create_sheet(sname or "Lớp")
                ws2.sheet_view.showGridLines = False

                # Banner
                ws2.merge_cells("A1:G1")
                c = ws2["A1"]
                c.value = f"BÁO CÁO ĐIỂM DANH — LỚP: {c_name}"
                c.font = Font(name="Calibri", size=14, bold=True, color=C_HDR_FG)
                c.fill = hdr_fill(C_HDR_BG)
                c.alignment = Alignment(horizontal="center", vertical="center")
                ws2.row_dimensions[1].height = 36

                # Dòng phụ đề
                ws2.merge_cells("A2:G2")
                c2 = ws2["A2"]
                c2.value = (f"Môn: {data.subject_name}   |   Ngày: {data.session_date}"
                           f"   |   Giờ: {data.start_time} – {data.end_time}"
                           f"   |   Giáo viên: {data.teacher_name or '—'}")
                c2.font = Font(name="Calibri", size=9, color="FFFFFF", italic=True)
                c2.fill = hdr_fill(C_SUB_BG)
                c2.alignment = Alignment(horizontal="center", vertical="center")
                ws2.row_dimensions[2].height = 18

                # Thống kê mini
                prs = (grp["status"]=="PRESENT").sum()
                abs_ = (grp["status"]=="ABSENT").sum()
                tot = len(grp)
                rate = prs/tot*100 if tot else 0
                ws2.merge_cells("A3:G3")
                sc = ws2["A3"]
                sc.value = (f"Sĩ số: {tot}   |   Có mặt: {prs}   |"
                           f"   Vắng: {abs_}   |   Tỉ lệ: {rate:.1f}%")
                sc.font = Font(name="Calibri", size=10, bold=True, color="1E3A5F")
                sc.fill = hdr_fill("E8F0FB")
                sc.alignment = Alignment(horizontal="center")
                ws2.row_dimensions[3].height = 20

                # Header bảng
                hdrs = ["STT","Mã HV","Họ và Tên","Giới tính","Trạng thái","Giờ điểm danh","Độ chính xác"]
                col_w = [6, 12, 32, 10, 12, 16, 14]
                for ci, (h, w) in enumerate(zip(hdrs, col_w), 1):
                    c = ws2.cell(row=4, column=ci, value=h)
                    c.font = Font(name="Calibri", bold=True, color=C_HDR_FG, size=10)
                    c.fill = hdr_fill(C_HDR_BG)
                    c.alignment = Alignment(horizontal="center", vertical="center")
                    c.border = cell_border()
                    ws2.column_dimensions[get_column_letter(ci)].width = w
                ws2.row_dimensions[4].height = 22

                # Freeze header
                ws2.freeze_panes = "A5"
                ws2.auto_filter.ref = f"A4:G4"

                # Data rows
                for idx, row_d in enumerate(grp.itertuples(index=False), 1):
                    is_p = row_d.status == "PRESENT"
                    bg = C_PRESENT if is_p else C_ABSENT
                    score_val = getattr(row_d, "recognition_score", 0) or 0
                    score_str = f"{float(score_val)*100:.1f}%" if is_p and score_val else "—"
                    time_str = str(row_d.check_in_time) if is_p else "—"

                    vals = [idx, row_d.student_code, row_d.full_name,
                            getattr(row_d,"gender",""),
                            "✓ Có mặt" if is_p else "✗ Vắng",
                            time_str, score_str]
                    rn = idx + 4
                    ws2.row_dimensions[rn].height = 20
                    for ci, val in enumerate(vals, 1):
                        c = ws2.cell(row=rn, column=ci, value=val)
                        c.fill = row_fill(bg)
                        c.border = cell_border(thin=False)
                        c.font = Font(name="Calibri", size=10)
                        if ci == 3:
                            c.alignment = Alignment(horizontal="left",
                                                    vertical="center", indent=1)
                        elif ci == 5:
                            c.font = Font(name="Calibri", size=10, bold=True,
                                         color=C_GREEN if is_p else C_RED)
                            c.alignment = Alignment(horizontal="center")
                        else:
                            c.alignment = Alignment(horizontal="center",
                                                    vertical="center")

        # Lưu file
        if not output_path:
            fname = (f"BaoCao_{data.class_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                     ).replace("/","-").replace("\\","-").replace(" ","_")
            output_path = str(report_config.output_dir / fname)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(output_path)
        logger.success(f"Excel saved: {output_path}")
        return output_path

    except ImportError:
        logger.error("Cần cài: pip install pandas openpyxl")
        return None
    except Exception as e:
        logger.error(f"export_excel error: {e}")
        return None


        # 1. Chuyển đổi dữ liệu sang Pandas DataFrame
        records = data.records or []
        if not records:
            df = pd.DataFrame(columns=["student_code", "full_name", "class_name", "gender", "status", "check_in_time", "building"])
        else:
            df = pd.DataFrame(records)

        # Đảm bảo các cột cần thiết tồn tại
        for col in ["building", "class_name", "status"]:
            if col not in df.columns:
                df[col] = "Khác"

        # Khởi tạo Workbook
        wb = Workbook()
        
        # Định dạng chung
        border_thin = Border(
            left=Side(style='thin', color="CCCCCC"), right=Side(style='thin', color="CCCCCC"),
            top=Side(style='thin', color="CCCCCC"), bottom=Side(style='thin', color="CCCCCC")
        )
        header_fill = PatternFill(start_color="2A313C", end_color="2A313C", fill_type="solid") # Dark Gray cho header bảng
        header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        title_fill = PatternFill(start_color="69E29C", end_color="69E29C", fill_type="solid") # Màu Xanh lá (Mint)
        title_font = Font(name="Arial", size=14, bold=True, color="FFFFFF")
        center_align = Alignment(horizontal="center", vertical="center")
        left_align = Alignment(horizontal="left", vertical="center")

        # ── 1. SHEET TỔNG HỢP (DASHBOARD) ──
        ws_dash = wb.active
        ws_dash.title = "Tổng hợp"
        
        ws_dash.merge_cells("A1:E1")
        c = ws_dash["A1"]
        c.value = "TỔNG HỢP ĐIỂM DANH THEO TÒA NHÀ"
        c.font = title_font
        c.fill = title_fill
        c.alignment = center_align
        ws_dash.row_dimensions[1].height = 35

        # Group theo Tòa nhà
        if not df.empty:
            df_bld = df.groupby('building').agg(
                Tổng_SV=('student_code', 'count'),
                Có_mặt=('status', lambda x: (x == 'PRESENT').sum()),
                Vắng=('status', lambda x: (x == 'ABSENT').sum())
            ).reset_index()
            df_bld['Tỉ_lệ_%'] = (df_bld['Có_mặt'] / df_bld['Tổng_SV'] * 100).round(1)
        else:
            df_bld = pd.DataFrame(columns=["building", "Tổng_SV", "Có_mặt", "Vắng", "Tỉ_lệ_%"])

        dash_headers = ["Tòa nhà", "Tổng số SV", "Có mặt", "Vắng mặt", "Tỉ lệ (%)"]
        for col_num, header in enumerate(dash_headers, 1):
            cell = ws_dash.cell(row=3, column=col_num)
            cell.value = header
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = border_thin

        for r_idx, row in enumerate(df_bld.itertuples(index=False), 4):
            for c_idx, val in enumerate(row, 1):
                cell = ws_dash.cell(row=r_idx, column=c_idx)
                cell.value = val
                cell.alignment = center_align
                cell.border = border_thin

        ws_dash.column_dimensions['A'].width = 25
        ws_dash.column_dimensions['B'].width = 15
        ws_dash.column_dimensions['C'].width = 15
        ws_dash.column_dimensions['D'].width = 15
        ws_dash.column_dimensions['E'].width = 15

        # ── 2. CÁC SHEET LỚP (Tạo sheet theo từng nhóm class_name) ──
        if df.empty:
            ws_empty = wb.create_sheet("Trống")
            ws_empty["A1"] = "Không có dữ liệu điểm danh"
        else:
            grouped = df.groupby("class_name")
            for c_name, group in grouped:
                # Tên sheet tối đa 31 ký tự, không chứa ký tự đặc biệt
                sheet_name = str(c_name)[:31].replace(":", "").replace("/", "").replace("\\", "").replace("?", "").replace("*", "").replace("[", "").replace("]", "")
                ws = wb.create_sheet(sheet_name)
                ws.sheet_view.showGridLines = False
                
                # Header Tiêu đề lớn
                ws.merge_cells("A1:F1")
                c = ws["A1"]
                c.value = f" BÁO CÁO ĐIỂM DANH LỚP: {c_name} - NGÀY {data.session_date}"
                c.font = title_font
                c.fill = title_fill
                c.alignment = left_align
                ws.row_dimensions[1].height = 35
                
                # Headers Bảng dữ liệu
                headers = ["STT", "Họ và tên", "MSSV", "Giới tính", "Thời gian", "Ghi chú"]
                for col_num, header in enumerate(headers, 1):
                    cell = ws.cell(row=3, column=col_num)
                    cell.value = header
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = center_align
                    cell.border = border_thin

                ws.column_dimensions['A'].width = 8
                ws.column_dimensions['B'].width = 30
                ws.column_dimensions['C'].width = 15
                ws.column_dimensions['D'].width = 12
                ws.column_dimensions['E'].width = 15
                ws.column_dimensions['F'].width = 15

                # Render Data Rows
                for idx, row in enumerate(group.itertuples(), 1):
                    is_p = row.status == "PRESENT"
                    ghi_chu = "Có mặt" if is_p else "Vắng"
                    time_str = str(row.check_in_time) if is_p else ""
                    
                    vals = [
                        idx,
                        row.full_name,
                        row.student_code,
                        row.gender,
                        time_str,
                        ghi_chu
                    ]
                    
                    row_num = 3 + idx
                    ws.row_dimensions[row_num].height = 25
                    for col_num, val in enumerate(vals, 1):
                        cell = ws.cell(row=row_num, column=col_num)
                        cell.value = val
                        cell.border = border_thin
                        
                        if col_num in [1, 3, 4, 5]: # STT, MSSV, Giới tính, Thời gian
                            cell.alignment = center_align
                        else: # Họ tên, Ghi chú
                            cell.alignment = left_align
                            if col_num == 2:
                                cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
                            
                        # Ghi chú (Màu sắc)
                        if col_num == 6:
                            # Chỉnh màu chữ cho "Ghi chú" (Có mặt = Xanh, Vắng = Xám nhạt/Đỏ)
                            cell.font = Font(color="2E7D32" if is_p else "9E9E9E", bold=True)
                            cell.alignment = center_align

        # Đường dẫn lưu file
        if not output_path:
            fname = (
                f"BaoCao_DiemDanh_{data.class_code}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            ).replace("/","-").replace("\\","-").replace(" ","_")
            output_path = str(report_config.output_dir / fname)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(output_path)
        logger.success(f"Excel saved: {output_path}")
        return output_path

    except ImportError:
        logger.error("Thư viện 'pandas' hoặc 'openpyxl' chưa được cài đặt. Hãy chạy: pip install pandas openpyxl")
        return None
    except Exception as e:
        logger.error(f"export_excel error: {e}")
        return None


# ─────────────────────────────────────────────
#  PDF Report  — Modern Light Theme
# ─────────────────────────────────────────────
def export_pdf(data: ReportData, output_path: str = None) -> Optional[str]:
    """
    Xuất PDF theo lớp:
    - Trang 1: Tổng hợp buổi học + thống kê
    - Mỗi lớp: Section header + mini stats + bảng điểm danh chi tiết
    - Font Arial (Windows) để hiện đúng tiếng Việt
    """
    try:
        import os
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph,
            Spacer, HRFlowable, PageBreak, KeepTogether,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        # ── Đăng ký font Unicode (Arial Windows → DejaVu fallback) ──────
        FONT      = "Helvetica"
        FONT_BOLD = "Helvetica-Bold"
        _registered = False
        _candidates = [
            (r"C:\Windows\Fonts\arial.ttf",   r"C:\Windows\Fonts\arialbd.ttf",   "Arial", "Arial-Bold"),
            (r"C:\Windows\Fonts\times.ttf",    r"C:\Windows\Fonts\timesbd.ttf",   "Times", "Times-Bold"),
        ]
        for reg, bold_reg, fname, fbold in _candidates:
            if os.path.exists(reg):
                try:
                    pdfmetrics.registerFont(TTFont(fname, reg))
                    if os.path.exists(bold_reg):
                        pdfmetrics.registerFont(TTFont(fbold, bold_reg))
                    else:
                        fbold = fname
                    FONT, FONT_BOLD = fname, fbold
                    _registered = True
                    break
                except Exception:
                    pass
        if not _registered:
            # Thử DejaVu (thường đi kèm reportlab)
            try:
                from reportlab.pdfbase.ttfonts import TTFont as _TTF
                import reportlab
                _djvu = os.path.join(os.path.dirname(reportlab.__file__),
                                     "fonts", "DejaVuSans.ttf")
                _djvu_b = os.path.join(os.path.dirname(reportlab.__file__),
                                       "fonts", "DejaVuSans-Bold.ttf")
                if os.path.exists(_djvu):
                    pdfmetrics.registerFont(_TTF("DejaVu", _djvu))
                    if os.path.exists(_djvu_b):
                        pdfmetrics.registerFont(_TTF("DejaVu-Bold", _djvu_b))
                    else:
                        _djvu_b_name = "DejaVu"
                    FONT, FONT_BOLD = "DejaVu", "DejaVu-Bold"
            except Exception:
                pass  # Dùng Helvetica — tiếng Việt có thể bị vỡ

        # ── Output path ───────────────────────────────────────────────
        if not output_path:
            fname_out = (
                f"BaoCao_{data.class_code}_{data.subject_name[:20]}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            ).replace("/","-").replace("\\","-").replace(" ","_")
            output_path = str(report_config.output_dir / fname_out)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # ── Màu sắc (light theme, in được) ───────────────────────────
        NAVY  = colors.HexColor("#1E3A5F")
        BLUE  = colors.HexColor("#2E6DA4")
        WHITE = colors.white
        LGRAY = colors.HexColor("#F5F8FC")
        GRAY  = colors.HexColor("#EEF2F8")
        BLINE = colors.HexColor("#C5D4E8")
        TXT   = colors.HexColor("#1A2B3C")
        DIM   = colors.HexColor("#5A7A9A")
        GREEN = colors.HexColor("#2E7D32")
        RED   = colors.HexColor("#C62828")
        AMBER = colors.HexColor("#E65100")

        rate_color = GREEN if data.attendance_rate >= 80 else \
                     AMBER if data.attendance_rate >= 60 else RED

        W = A4[0] - 3*cm

        # ── Style helpers ─────────────────────────────────────────────
        def S(name, **kw):
            return ParagraphStyle(name, fontName=FONT, **kw)

        def SB(name, **kw):
            return ParagraphStyle(name, fontName=FONT_BOLD, **kw)

        sT   = SB("t",   fontSize=16, textColor=WHITE,  alignment=TA_CENTER)
        sS   = S("s",    fontSize=9,  textColor=WHITE,  alignment=TA_CENTER)
        sSec = SB("sc",  fontSize=11, textColor=NAVY,   alignment=TA_LEFT,
                  spaceBefore=6, spaceAfter=4)
        sC   = S("c",    fontSize=9,  textColor=TXT,    alignment=TA_CENTER)
        sCL  = S("cl",   fontSize=9,  textColor=TXT,    alignment=TA_LEFT)
        sFt  = S("f",    fontSize=7,  textColor=DIM,    alignment=TA_CENTER)
        sLb  = SB("lb",  fontSize=8,  textColor=DIM,    alignment=TA_CENTER)
        sHdr = SB("hdr", fontSize=9,  textColor=WHITE,  alignment=TA_CENTER)
        sKey = SB("k",   fontSize=9,  textColor=DIM,    alignment=TA_LEFT)

        def hx(c):
            h = c.hexval()
            return f"#{h[2:]}" if h.startswith("0x") else f"#{h}"

        story = []

        # ═══════════════════════════════════════════════════════════════
        # PHẦN 1 — BANNER + THÔNG TIN BUỔI HỌC + THỐNG KÊ TỔNG
        # ═══════════════════════════════════════════════════════════════
        banner = Table(
            [[Paragraph("HE THONG DIEM DANH KHUON MAT", sT)],
             [Paragraph(f"BAO CAO DIEM DANH — {data.subject_name}", sS)]],
            colWidths=[W]
        )
        banner.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), NAVY),
            ("TOPPADDING",    (0,0),(-1,-1), 12),
            ("BOTTOMPADDING", (0,0),(-1,-1), 10),
            ("LINEBELOW",     (0,-1),(-1,-1), 3, BLUE),
        ]))
        story += [banner, Spacer(1, 10)]

        # Bảng thông tin buổi học
        info_rows = [
            [Paragraph("Lop hoc:", sKey),
             Paragraph(f"{data.class_code} — {data.class_name}", sCL),
             Paragraph("Ngay:", sKey),
             Paragraph(data.session_date, sC)],
            [Paragraph("Mon hoc:", sKey),
             Paragraph(data.subject_name, sCL),
             Paragraph("Gio:", sKey),
             Paragraph(f"{data.start_time} — {data.end_time}", sC)],
            [Paragraph("Giao vien:", sKey),
             Paragraph(data.teacher_name or "—", sCL),
             Paragraph("Session ID:", sKey),
             Paragraph(str(data.session_id), sC)],
        ]
        info_tbl = Table(info_rows, colWidths=[2.2*cm, 8*cm, 2.2*cm, 3.1*cm])
        info_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0),(-1,-1), LGRAY),
            ("BACKGROUND", (0,0),(0,-1),  GRAY),
            ("BACKGROUND", (2,0),(2,-1),  GRAY),
            ("BOX",        (0,0),(-1,-1), 0.5, BLINE),
            ("INNERGRID",  (0,0),(-1,-1), 0.3, BLINE),
            ("TOPPADDING", (0,0),(-1,-1), 5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",(0,0),(-1,-1), 6),
        ]))
        story += [info_tbl, Spacer(1, 10)]

        # Thống kê tổng
        stats_tbl = Table([
            [Paragraph("TONG HOC VIEN", sLb), Paragraph("CO MAT", sLb),
             Paragraph("VANG MAT", sLb),      Paragraph("TI LE", sLb)],
            [Paragraph(f'<font color="{hx(NAVY)}" size="20"><b>{data.total_students}</b></font>', sC),
             Paragraph(f'<font color="{hx(GREEN)}" size="20"><b>{data.present_count}</b></font>', sC),
             Paragraph(f'<font color="{hx(RED)}" size="20"><b>{data.absent_count}</b></font>', sC),
             Paragraph(f'<font color="{hx(rate_color)}" size="20"><b>{data.attendance_rate:.1f}%</b></font>', sC)],
        ], colWidths=[W/4]*4)
        stats_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  GRAY),
            ("BACKGROUND",    (0,1),(-1,1),  WHITE),
            ("BOX",           (0,0),(-1,-1), 1, NAVY),
            ("LINEBELOW",     (0,0),(-1,0),  0.5, BLINE),
            ("INNERGRID",     (0,0),(-1,-1), 0.3, BLINE),
            ("TOPPADDING",    (0,0),(-1,-1), 8),
            ("BOTTOMPADDING", (0,0),(-1,-1), 8),
            ("ALIGN",         (0,0),(-1,-1), "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ]))
        story += [stats_tbl, Spacer(1, 14)]

        # ═══════════════════════════════════════════════════════════════
        # PHẦN 2 — BẢNG ĐIỂM DANH THEO TỪNG LỚP
        # ═══════════════════════════════════════════════════════════════
        records = data.records or []

        # Group by class_name
        from collections import defaultdict
        class_groups = defaultdict(list)
        for r in records:
            cname = r.get("class_name") or "Khong ro lop"
            class_groups[cname].append(r)

        for class_idx, (cname, class_records) in enumerate(sorted(class_groups.items())):
            prs  = sum(1 for r in class_records if r.get("status") == "PRESENT")
            abs_ = len(class_records) - prs
            rate = prs / len(class_records) * 100 if class_records else 0
            rc   = GREEN if rate >= 80 else AMBER if rate >= 60 else RED

            # Ngăn cách giữa các lớp
            if class_idx > 0:
                story.append(Spacer(1, 16))
                story.append(HRFlowable(width="100%", thickness=0.5, color=BLINE))
                story.append(Spacer(1, 8))

            # Section header lớp
            cls_banner = Table(
                [[Paragraph(f"LOP: {cname}", SB("cb", fontSize=11, textColor=WHITE,
                                                 alignment=TA_LEFT))]],
                colWidths=[W]
            )
            cls_banner.setStyle(TableStyle([
                ("BACKGROUND",    (0,0),(-1,-1), BLUE),
                ("TOPPADDING",    (0,0),(-1,-1), 6),
                ("BOTTOMPADDING", (0,0),(-1,-1), 6),
                ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ]))

            # Mini stats lớp
            cls_stat = Table([
                [Paragraph("Si so:", sKey),    Paragraph(str(len(class_records)), sC),
                 Paragraph("Co mat:", sKey),   Paragraph(str(prs), sC),
                 Paragraph("Vang:", sKey),     Paragraph(str(abs_), sC),
                 Paragraph("Ti le:", sKey),    Paragraph(f"{rate:.1f}%", sC)],
            ], colWidths=[1.5*cm,1*cm, 1.7*cm,1*cm, 1.3*cm,1*cm, 1.3*cm,1.5*cm])
            cls_stat.setStyle(TableStyle([
                ("BACKGROUND",    (0,0),(-1,-1), LGRAY),
                ("BOX",           (0,0),(-1,-1), 0.5, BLINE),
                ("INNERGRID",     (0,0),(-1,-1), 0.2, BLINE),
                ("TOPPADDING",    (0,0),(-1,-1), 4),
                ("BOTTOMPADDING", (0,0),(-1,-1), 4),
                ("LEFTPADDING",   (0,0),(-1,-1), 5),
                ("TEXTCOLOR",     (1,0),(1,0),   NAVY),
                ("TEXTCOLOR",     (3,0),(3,0),   GREEN),
                ("TEXTCOLOR",     (5,0),(5,0),   RED),
                ("TEXTCOLOR",     (7,0),(7,0),   rc),
                ("FONTNAME",      (1,0),(-1,0),  FONT_BOLD),
            ]))

            # Bảng chi tiết học viên trong lớp
            hdrs = [Paragraph(h, sHdr)
                    for h in ["STT","Ma HV","Ho va Ten","Trang thai","Gio vao","Chinh xac"]]
            rows = [hdrs]
            for idx, r in enumerate(class_records, 1):
                is_p  = r.get("status") == "PRESENT"
                score = r.get("recognition_score", 0) or 0
                st_para = Paragraph(
                    f'<font color="{hx(GREEN)}"><b>Co mat</b></font>' if is_p
                    else f'<font color="{hx(RED)}"><b>Vang</b></font>', sC)
                rows.append([
                    Paragraph(str(idx), sC),
                    Paragraph(str(r.get("student_code","")), sC),
                    Paragraph(str(r.get("full_name","")), sCL),
                    st_para,
                    Paragraph(str(r.get("check_in_time","—")), sC),
                    Paragraph(f"{float(score)*100:.0f}%" if is_p and score else "—", sC),
                ])

            dt = Table(rows,
                       colWidths=[0.9*cm, 2.2*cm, 6.5*cm, 2.8*cm, 2.4*cm, 2.2*cm],
                       repeatRows=1)
            dt_style = [
                ("BACKGROUND",    (0,0), (-1,0),  NAVY),
                ("LINEBELOW",     (0,0), (-1,0),  1.5, BLUE),
                ("BOX",           (0,0), (-1,-1), 0.5, BLINE),
                ("INNERGRID",     (0,0), (-1,-1), 0.2, BLINE),
                ("TOPPADDING",    (0,0), (-1,-1), 4),
                ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                ("LEFTPADDING",   (0,0), (-1,-1), 4),
                ("ALIGN",         (0,0), (-1,-1), "CENTER"),
                ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
                ("ALIGN",         (2,1), (2,-1),  "LEFT"),
            ]
            for i in range(1, len(rows)):
                bg = LGRAY if i % 2 == 0 else WHITE
                dt_style.append(("BACKGROUND", (0,i), (-1,i), bg))
            dt.setStyle(TableStyle(dt_style))

            story += [
                KeepTogether([cls_banner, Spacer(1,4), cls_stat, Spacer(1,6)]),
                dt,
            ]

        # ── Footer ────────────────────────────────────────────────────
        story += [
            Spacer(1, 16),
            HRFlowable(width="100%", thickness=0.5, color=BLINE),
            Spacer(1, 4),
            Paragraph(
                f"Xuat luc {datetime.now().strftime('%H:%M:%S %d/%m/%Y')} "
                f"· Session ID: {data.session_id} · He thong Diem danh Khuon mat",
                sFt),
        ]

        # ── Build ─────────────────────────────────────────────────────
        doc = SimpleDocTemplate(
            output_path, pagesize=A4,
            leftMargin=1.5*cm, rightMargin=1.5*cm,
            topMargin=1.5*cm, bottomMargin=1.5*cm,
            title=f"Bao cao diem danh — {data.subject_name}",
        )
        doc.build(story)
        logger.success(f"PDF saved: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"export_pdf error: {e}", exc_info=True)
        return None
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

        if not output_path:
            fname = (f"BaoCao_{data.class_code}_{data.subject_name[:20]}_"
                     f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                     ).replace("/","-").replace("\\","-").replace(" ","_")
            output_path = str(report_config.output_dir / fname)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # ── Bảng màu (light/print-friendly) ───────────────────────────
        NAVY    = colors.HexColor("#1E3A5F")
        BLUE    = colors.HexColor("#2E6DA4")
        WHITE   = colors.white
        LGRAY   = colors.HexColor("#F5F8FC")   # alternating row nhạt
        GRAY    = colors.HexColor("#EEF2F8")   # row chẵn
        BLINE   = colors.HexColor("#C5D4E8")   # border nhạt
        TXT     = colors.HexColor("#1A2B3C")   # text chính
        DIM     = colors.HexColor("#5A7A9A")   # text phụ
        GREEN   = colors.HexColor("#2E7D32")
        RED     = colors.HexColor("#C62828")
        AMBER   = colors.HexColor("#E65100")

        rate_color = GREEN if data.attendance_rate >= 80 else \
                     AMBER if data.attendance_rate >= 60 else RED

        W = A4[0] - 3*cm

        def S(name, **kw): return ParagraphStyle(name, **kw)
        sT  = S("t",  fontSize=18, fontName="Helvetica-Bold", textColor=WHITE, alignment=TA_CENTER)
        sS  = S("s",  fontSize=10, fontName="Helvetica",      textColor=WHITE, alignment=TA_CENTER)
        sSec= S("sc", fontSize=11, fontName="Helvetica-Bold", textColor=NAVY,  alignment=TA_LEFT, spaceBefore=8, spaceAfter=4)
        sC  = S("c",  fontSize=9,  fontName="Helvetica",      textColor=TXT,   alignment=TA_CENTER)
        sCL = S("cl", fontSize=9,  fontName="Helvetica",      textColor=TXT,   alignment=TA_LEFT)
        sFt = S("f",  fontSize=7.5,fontName="Helvetica",      textColor=DIM,   alignment=TA_CENTER)
        sLb = S("lb", fontSize=8,  fontName="Helvetica-Bold", textColor=DIM,   alignment=TA_CENTER)
        sVl = S("vl", fontSize=22, fontName="Helvetica-Bold", textColor=TXT,   alignment=TA_CENTER)

        story = []

        # ── Banner tiêu đề ────────────────────────────────────────────
        banner = Table(
            [[Paragraph("HỆ THỐNG ĐIỂM DANH KHUÔN MẶT", sT)],
             [Paragraph(f"BÁO CÁO ĐIỂM DANH — {data.subject_name}", sS)]],
            colWidths=[W]
        )
        banner.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), NAVY),
            ("TOPPADDING",    (0,0),(-1,-1), 10),
            ("BOTTOMPADDING", (0,0),(-1,-1), 10),
            ("LINEBELOW",     (0,-1),(-1,-1), 3, BLUE),
        ]))
        story += [banner, Spacer(1, 10)]

        # ── Bảng thông tin ────────────────────────────────────────────
        info_rows = [
            [Paragraph("<b>Lớp học:</b>", S("il",fontSize=9,fontName="Helvetica-Bold",textColor=DIM)),
             Paragraph(f"{data.class_code} — {data.class_name}", sCL),
             Paragraph("<b>Ngày:</b>", S("il2",fontSize=9,fontName="Helvetica-Bold",textColor=DIM)),
             Paragraph(data.session_date, sC)],
            [Paragraph("<b>Môn học:</b>", S("il3",fontSize=9,fontName="Helvetica-Bold",textColor=DIM)),
             Paragraph(data.subject_name, sCL),
             Paragraph("<b>Giờ:</b>", S("il4",fontSize=9,fontName="Helvetica-Bold",textColor=DIM)),
             Paragraph(f"{data.start_time} — {data.end_time}", sC)],
            [Paragraph("<b>Giáo viên:</b>", S("il5",fontSize=9,fontName="Helvetica-Bold",textColor=DIM)),
             Paragraph(data.teacher_name or "—", sCL),
             Paragraph("<b>Session:</b>", S("il6",fontSize=9,fontName="Helvetica-Bold",textColor=DIM)),
             Paragraph(str(data.session_id), sC)],
        ]
        info_tbl = Table(info_rows, colWidths=[2.5*cm, 7.5*cm, 2*cm, 3.5*cm])
        info_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0),(-1,-1), LGRAY),
            ("BACKGROUND", (0,0),(0,-1),  GRAY),
            ("BACKGROUND", (2,0),(2,-1),  GRAY),
            ("BOX",        (0,0),(-1,-1), 0.5, BLINE),
            ("INNERGRID",  (0,0),(-1,-1), 0.3, BLINE),
            ("TOPPADDING", (0,0),(-1,-1), 5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING",(0,0),(-1,-1), 6),
        ]))
        story += [info_tbl, Spacer(1, 12)]

        # ── Stats cards ───────────────────────────────────────────────
        def stat_cell(label, value, color):
            return [
                Paragraph(label, sLb),
                Paragraph(f'<font color="#{color.hexval()[2:]}">{value}</font>', sVl)
            ]
        # openpyxl color → use hex string directly for reportlab
        def hx(c): return f"#{c.hexval()[2:]}"

        stats_tbl = Table([
            [Paragraph("TỔNG HỌC VIÊN", sLb), Paragraph("CÓ MẶT", sLb),
             Paragraph("VẮNG MẶT", sLb),      Paragraph("TỈ LỆ", sLb)],
            [Paragraph(f'<font color="{hx(NAVY)}" size="20"><b>{data.total_students}</b></font>', sC),
             Paragraph(f'<font color="{hx(GREEN)}" size="20"><b>{data.present_count}</b></font>', sC),
             Paragraph(f'<font color="{hx(RED)}" size="20"><b>{data.absent_count}</b></font>', sC),
             Paragraph(f'<font color="{hx(rate_color)}" size="20"><b>{data.attendance_rate:.1f}%</b></font>', sC)],
        ], colWidths=[W/4]*4)
        stats_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  GRAY),
            ("BACKGROUND",    (0,1),(-1,1),  WHITE),
            ("BOX",           (0,0),(-1,-1), 1, NAVY),
            ("LINEBELOW",     (0,0),(-1,0),  0.5, BLINE),
            ("INNERGRID",     (0,0),(-1,-1), 0.3, BLINE),
            ("TOPPADDING",    (0,0),(-1,-1), 8),
            ("BOTTOMPADDING", (0,0),(-1,-1), 8),
            ("ALIGN",         (0,0),(-1,-1), "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ]))
        story += [stats_tbl, Spacer(1, 14)]

        # ── Bảng điểm danh chi tiết ───────────────────────────────────
        story.append(Paragraph("BẢNG ĐIỂM DANH CHI TIẾT", sSec))
        hdr_style = S("h", fontSize=9, fontName="Helvetica-Bold", textColor=WHITE, alignment=TA_CENTER)
        hdrs = [Paragraph(h, hdr_style)
                for h in ["STT","Mã HV","Họ và Tên","Lớp","Trạng thái","Giờ vào","Chính xác"]]
        rows = [hdrs]
        for idx, r in enumerate(data.records or [], 1):
            is_p = r.get("status") == "PRESENT"
            score = r.get("recognition_score", 0) or 0
            st_para = Paragraph(
                f'<font color="{hx(GREEN)}"><b>✓ Có mặt</b></font>' if is_p
                else f'<font color="{hx(RED)}"><b>✗ Vắng</b></font>', sC)
            rows.append([
                Paragraph(str(idx), sC),
                Paragraph(r.get("student_code",""), sC),
                Paragraph(r.get("full_name",""), sCL),
                Paragraph(r.get("class_name","—"), sC),
                st_para,
                Paragraph(str(r.get("check_in_time","—")), sC),
                Paragraph(f"{float(score)*100:.0f}%" if is_p and score else "—", sC),
            ])

        dt = Table(rows, colWidths=[0.9*cm,2.1*cm,5.2*cm,2.5*cm,2.4*cm,2.4*cm,2*cm], repeatRows=1)
        row_bgs = [LGRAY if i%2==0 else WHITE for i in range(len(rows)-1)]
        dt_style = [
            ("BACKGROUND",    (0,0), (-1,0),  NAVY),
            ("LINEBELOW",     (0,0), (-1,0),  2, BLUE),
            ("BOX",           (0,0), (-1,-1), 0.5, BLINE),
            ("INNERGRID",     (0,0), (-1,-1), 0.2, BLINE),
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 4),
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN",         (2,1), (2,-1),  "LEFT"),
        ]
        for i, bg in enumerate(row_bgs, 1):
            dt_style.append(("BACKGROUND", (0,i), (-1,i), bg))
        dt.setStyle(TableStyle(dt_style))
        story += [dt, Spacer(1, 16)]

        # ── Footer ────────────────────────────────────────────────────
        story.append(HRFlowable(width="100%", thickness=0.5, color=BLINE))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"Xuất lúc {datetime.now().strftime('%H:%M:%S %d/%m/%Y')} · Session ID: {data.session_id} · "
            f"Hệ thống Điểm danh Khuôn mặt", sFt))

        doc = SimpleDocTemplate(
            output_path, pagesize=A4,
            leftMargin=1.5*cm, rightMargin=1.5*cm,
            topMargin=1.5*cm, bottomMargin=1.5*cm,
        )
        doc.build(story)
        logger.success(f"PDF saved: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"export_pdf error: {e}")
        return None


        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        CLR_PANEL   = colors.HexColor("#0D1320"); CLR_CARD  = colors.HexColor("#111B2E")
        CLR_CYAN    = colors.HexColor("#06C8E8"); CLR_GREEN = colors.HexColor("#10D98A")
        CLR_RED     = colors.HexColor("#F04060"); CLR_TEXT  = colors.HexColor("#DCE8F8")
        CLR_DIM     = colors.HexColor("#8BA4C0"); CLR_DARK  = colors.HexColor("#4A6080")

        doc = SimpleDocTemplate(
            output_path, pagesize=A4,
            leftMargin=1.5*cm, rightMargin=1.5*cm,
            topMargin=1.5*cm,  bottomMargin=1.5*cm,
        )

        def sty(name, **kw):
            return ParagraphStyle(name, **kw)

        S_TITLE   = sty("t", fontSize=18, fontName="Helvetica-Bold",
                        textColor=CLR_CYAN, alignment=TA_CENTER, spaceAfter=4)
        S_SUB     = sty("s", fontSize=13, fontName="Helvetica-Bold",
                        textColor=CLR_TEXT, alignment=TA_CENTER, spaceAfter=8)
        S_SECTION = sty("sec", fontSize=11, fontName="Helvetica-Bold",
                        textColor=CLR_CYAN, alignment=TA_LEFT, spaceBefore=8)
        S_FOOTER  = sty("f", fontSize=8, fontName="Helvetica",
                        textColor=CLR_DARK, alignment=TA_CENTER)
        S_CELL    = sty("c",  fontSize=9, fontName="Helvetica",
                        textColor=CLR_TEXT, alignment=TA_CENTER)
        S_CELL_L  = sty("cl", fontSize=9, fontName="Helvetica",
                        textColor=CLR_TEXT, alignment=TA_LEFT)

        W = A4[0] - 3*cm
        story = []

        story.append(Paragraph("HỆ THỐNG ĐIỂM DANH KHUÔN MẶT", S_TITLE))
        story.append(Paragraph(data.title, S_SUB))
        story.append(HRFlowable(width="100%", thickness=1, color=CLR_CYAN, spaceAfter=8))

        info = [
            ["Lớp học:", f"{data.class_code} — {data.class_name}", "Ngày:",    data.session_date],
            ["Môn học:", data.subject_name,                         "Giờ:",     f"{data.start_time} — {data.end_time}"],
            ["Giáo viên:", data.teacher_name or "—",               "Session:", str(data.session_id)],
        ]
        info_tbl = Table(info, colWidths=[2.2*cm, 7*cm, 2*cm, 4*cm])
        info_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (-1,-1), CLR_PANEL),
            ("TEXTCOLOR",    (0,0), (0,-1),  CLR_DIM),
            ("TEXTCOLOR",    (2,0), (2,-1),  CLR_DIM),
            ("TEXTCOLOR",    (1,0), (1,-1),  CLR_TEXT),
            ("TEXTCOLOR",    (3,0), (3,-1),  CLR_TEXT),
            ("FONTNAME",     (0,0), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE",     (0,0), (-1,-1), 9),
            ("TOPPADDING",   (0,0), (-1,-1), 5),
            ("BOTTOMPADDING",(0,0), (-1,-1), 5),
            ("LEFTPADDING",  (0,0), (-1,-1), 6),
            ("GRID",         (0,0), (-1,-1), 0.3, CLR_DARK),
        ]))
        story.append(info_tbl)
        story.append(Spacer(1, 12))

        rc = "#10D98A" if data.attendance_rate >= 80 else \
             "#F59E0B" if data.attendance_rate >= 60 else "#F04060"
        stats_data = [[
            Paragraph(f"<font color='#8BA4C0' size='8'>TỔNG HỌC VIÊN</font><br/>"
                      f"<font color='#06C8E8' size='22'><b>{data.total_students}</b></font>", S_CELL),
            Paragraph(f"<font color='#8BA4C0' size='8'>CÓ MẶT</font><br/>"
                      f"<font color='#10D98A' size='22'><b>{data.present_count}</b></font>", S_CELL),
            Paragraph(f"<font color='#8BA4C0' size='8'>VẮNG MẶT</font><br/>"
                      f"<font color='#F04060' size='22'><b>{data.absent_count}</b></font>", S_CELL),
            Paragraph(f"<font color='#8BA4C0' size='8'>TỈ LỆ</font><br/>"
                      f"<font color='{rc}' size='22'><b>{data.attendance_rate:.1f}%</b></font>", S_CELL),
        ]]
        st = Table(stats_data, colWidths=[W/4]*4)
        st.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (-1,-1), CLR_CARD),
            ("TOPPADDING",   (0,0), (-1,-1), 10),
            ("BOTTOMPADDING",(0,0), (-1,-1), 10),
            ("LINEABOVE",    (0,0), (-1,0),  1, CLR_CYAN),
            ("LINEBELOW",    (0,-1),(-1,-1), 1, CLR_CYAN),
            ("INNERGRID",    (0,0), (-1,-1), 0.5, CLR_DARK),
            ("ALIGN",        (0,0), (-1,-1), "CENTER"),
            ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ]))
        story.append(st)
        story.append(Spacer(1, 14))

        story.append(Paragraph("BẢNG ĐIỂM DANH CHI TIẾT", S_SECTION))
        story.append(Spacer(1, 6))

        hdrs = [Paragraph(f"<b>{h}</b>", S_CELL)
                for h in ["STT","Mã HV","Họ và Tên","Trạng thái","Giờ điểm danh","Độ chính xác"]]
        tbl_data = [hdrs]
        for idx, r in enumerate(data.records or [], 1):
            is_p = r.get("status") == "PRESENT"
            st_txt = f'<font color="#10D98A">✓ Có mặt</font>' if is_p \
                     else f'<font color="#F04060">✗ Vắng</font>'
            tbl_data.append([
                Paragraph(str(idx), S_CELL),
                Paragraph(r.get("student_code",""), S_CELL),
                Paragraph(r.get("full_name",""), S_CELL_L),
                Paragraph(st_txt, S_CELL),
                Paragraph(str(r.get("check_in_time","—")), S_CELL),
                Paragraph(f"{r.get('recognition_score',0)*100:.1f}%" if is_p else "—", S_CELL),
            ])

        dt = Table(tbl_data, colWidths=[1*cm,2.2*cm,6*cm,2.8*cm,3*cm,2.5*cm], repeatRows=1)
        dt.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0),  CLR_PANEL),
            ("LINEBELOW",     (0,0), (-1,0),  1.5, CLR_CYAN),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [CLR_CARD, CLR_PANEL]),
            ("GRID",          (0,0), (-1,-1), 0.3, CLR_DARK),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING",   (0,0), (-1,-1), 4),
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN",         (2,1), (2,-1),  "LEFT"),
        ]))
        story.append(dt)
        story.append(Spacer(1, 20))

        story.append(HRFlowable(width="100%", thickness=0.5, color=CLR_DARK))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"Báo cáo tạo lúc {datetime.now().strftime('%H:%M:%S %d/%m/%Y')} "
            f"· Session ID: {data.session_id}",
            S_FOOTER
        ))

        doc.build(story)
        logger.success(f"PDF saved: {output_path}")
        return output_path



# ─────────────────────────────────────────────
def generate_report(session_id: int, fmt: str = "both") -> dict:
    data = load_report_data(session_id)
    if not data:
        return {"success": False, "error": f"Không tìm thấy session {session_id}"}
    result = {"success": True, "excel": None, "pdf": None}
    if fmt in ("excel", "both"):
        result["excel"] = export_excel(data)
    if fmt in ("pdf", "both"):
        result["pdf"]   = export_pdf(data)
    if not any([result["excel"], result["pdf"]]):
        result["success"] = False
        result["error"]   = "Xuất file thất bại"
    return result