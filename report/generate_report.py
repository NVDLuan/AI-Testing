import os
import re
import sys
import json
import glob

# Ensure UTF-8 output on Windows (bỏ qua nếu stdout không hỗ trợ reconfigure)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

def find_latest_json(raw_data_dir):
    search_path = os.path.join(raw_data_dir, "test_results_*.json")
    files = glob.glob(search_path)
    if not files:
        return None
    # Sort files by modification time
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]

def generate_report(json_path, output_path, template_path):
    print(f"[*] Đang đọc dữ liệu JSON từ: {json_path}")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[x] Lỗi khi đọc file JSON: {e}")
        return False

    print(f"[*] Đang đọc template HTML từ: {template_path}")
    if not os.path.exists(template_path):
        print(f"[x] Không tìm thấy file template HTML tại: {template_path}")
        return False
        
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()
    except Exception as e:
        print(f"[x] Lỗi khi đọc file template: {e}")
        return False

    # Dump JSON as safe string preserving Vietnamese UTF-8 characters
    print("[*] Đang biên dịch và tích hợp dữ liệu...")
    json_data_str = json.dumps(data, ensure_ascii=False, indent=2)
    
    # Replace placeholder in template. Template dùng "{{ TEST_REPORT_DATA_PLACEHOLDER }}"
    # (có thể kèm/không kèm khoảng trắng) -> regex chịu được cả 2 dạng.
    placeholder_re = re.compile(r"\{\{\s*TEST_REPORT_DATA_PLACEHOLDER\s*\}\}")
    if not placeholder_re.search(template_content):
        print("[x] Không tìm thấy placeholder {{ TEST_REPORT_DATA_PLACEHOLDER }} trong template!")
        return False
    compiled_content = placeholder_re.sub(lambda _m: json_data_str, template_content)

    print(f"[*] Đang ghi báo cáo ra file: {output_path}")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(compiled_content)
        print(f"[+] Đã tạo báo cáo kiểm thử thành công!")
        print(f"[+] Đường dẫn báo cáo: {os.path.abspath(output_path)}")
        return True
    except Exception as e:
        print(f"[x] Lỗi khi ghi file báo cáo: {e}")
        return False

def main():
    from pathlib import Path

    workspace_dir = Path(__file__).resolve().parent.parent
    raw_data_dir = os.path.join(workspace_dir, "reports")
    template_path = os.path.join(workspace_dir, "report/dashboard_template.html")
    
    # Defaults
    input_json = None
    output_html = os.path.join(workspace_dir, "test_report_dashboard.html")

    # Command line arguments
    if len(sys.argv) > 1:
        input_json = sys.argv[1]
    if len(sys.argv) > 2:
        output_html = sys.argv[2]

    # Resolve latest JSON if not provided
    if not input_json:
        print(f"[*] Đang tự động tìm kiếm file kết quả JSON mới nhất trong: {raw_data_dir}")
        input_json = find_latest_json(raw_data_dir)
        if not input_json:
            print("[x] Không tìm thấy file JSON kết quả nào trong thư mục raw_data_output!")
            print("Vui lòng cung cấp đường dẫn file thủ công: python generate_report.py <path_to_json>")
            sys.exit(1)
    
    if not os.path.exists(input_json):
        print(f"[x] File JSON được chỉ định không tồn tại: {input_json}")
        sys.exit(1)

    success = generate_report(input_json, output_html, template_path)
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
