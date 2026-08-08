"""
PDF Report Generator — Export investigation report.
Uses fpdf2 with Tahoma font for Somali character support.
"""
import os
from datetime import datetime
from fpdf import FPDF


class ReportPDF(FPDF):
    """Custom PDF class for the investigation report."""

    def __init__(self):
        super().__init__()
        font_path = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'tahoma.ttf')
        font_bold = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'tahomabd.ttf')
        self.add_font('Tahoma', '', font_path, uni=True)
        self.add_font('Tahoma', 'B', font_bold, uni=True)
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        self.set_font('Tahoma', 'B', 10)
        self.set_text_color(99, 102, 241)
        self.cell(0, 8, 'Somali Misinformation Detection System', align='L')
        self.set_text_color(120, 120, 120)
        self.set_font('Tahoma', '', 8)
        self.cell(0, 8, 'Investigation Report', align='R', new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(220, 220, 220)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font('Tahoma', '', 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 5, f'Generated: {datetime.now().strftime("%d %b %Y %H:%M")} | System Version: 1.0 | AI-Assisted Somali Political Misinformation Detection', align='C')

    def section_title(self, title):
        self.set_font('Tahoma', 'B', 11)
        self.set_text_color(55, 65, 81)
        self.set_fill_color(248, 250, 252)
        self.cell(0, 8, title, new_x='LMARGIN', new_y='NEXT', fill=True)
        self.ln(2)

    def section_body(self, text, bold=False):
        style = 'B' if bold else ''
        self.set_font('Tahoma', style, 10)
        self.set_text_color(75, 85, 99)
        self.multi_cell(0, 6, text)
        self.ln(2)

    def prediction_badge(self, prediction, confidence):
        colors = {
            'Reliable': (16, 185, 129),
            'Misleading': (245, 158, 11),
            'Misinformation': (239, 68, 68),
        }
        r, g, b = colors.get(prediction, (100, 100, 100))
        self.set_font('Tahoma', 'B', 14)
        self.set_text_color(r, g, b)
        self.cell(0, 10, f'{prediction} ({confidence:.1f}%)', new_x='LMARGIN', new_y='NEXT')
        self.ln(2)

    def match_entry(self, item):
        sim = item.get('similarity', 0)
        if sim >= 80:
            self.set_text_color(16, 185, 129)
        elif sim >= 50:
            self.set_text_color(245, 158, 11)
        else:
            self.set_text_color(150, 150, 150)

        self.set_font('Tahoma', 'B', 10)
        self.cell(25, 6, f'{sim}%', new_x='END')
        self.set_text_color(75, 85, 99)
        self.set_font('Tahoma', '', 9)
        platform = item.get('platform', 'Website')
        source = item.get('source', 'Unknown')
        date = item.get('date', '') or 'Unknown Date'
        if date and len(date) > 10:
            date = date[:10]
        self.cell(0, 6, f'{platform} | {source} | {date}', new_x='LMARGIN', new_y='NEXT')

        self.set_font('Tahoma', '', 8)
        self.set_text_color(100, 100, 100)
        reason = item.get('reason', '')
        if reason:
            self.multi_cell(0, 5, f'Reason: {reason}')

        url = item.get('url', '')
        if url:
            self.set_font('Tahoma', '', 7)
            self.set_text_color(99, 102, 241)
            self.cell(0, 4, url, new_x='LMARGIN', new_y='NEXT')

        self.ln(3)


def generate_report(prediction, confidence, main_claim, search_queries, similar_content):
    """Generate a PDF investigation report and return the file path."""
    pdf = ReportPDF()
    pdf.add_page()

    pdf.section_title('Prediction')
    pdf.prediction_badge(prediction, confidence)

    pdf.section_title('Main Claim')
    pdf.section_body(main_claim or 'No claim extracted.')

    pdf.section_title('Generated Search Queries')
    if search_queries:
        for i, q in enumerate(search_queries, 1):
            pdf.set_font('Tahoma', '', 10)
            pdf.set_text_color(75, 85, 99)
            pdf.cell(0, 6, f'Query {i}: {q}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(2)
    else:
        pdf.section_body('No queries generated.')

    pdf.section_title(f'Similar Content ({len(similar_content)} results)')
    if similar_content:
        for item in similar_content:
            pdf.match_entry(item)
    else:
        pdf.section_body('No similar content found.')

    report_dir = os.path.join(os.path.dirname(__file__), 'static', 'reports')
    os.makedirs(report_dir, exist_ok=True)
    filename = f'report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
    filepath = os.path.join(report_dir, filename)
    pdf.output(filepath)

    return filepath, filename
