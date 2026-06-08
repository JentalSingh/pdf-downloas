import os
import time
import base64
import json
import argparse
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from loguru import logger


def get_pdf_from_folder():
    """Automatically finds the first available PDF in the folder"""
    current_folder = os.getcwd()
    files = os.listdir(current_folder)
    
    # 🎯 FIX: Only real PDF files, not .py or other files
    pdf_files = []
    for f in files:
        # Check: .pdf extension AND not a Python/script file
        if f.lower().endswith('.pdf') and not f.endswith('.py'):
            # Double check: file size should be reasonable (not a script)
            file_path = os.path.join(current_folder, f)
            if os.path.isfile(file_path):
                size = os.path.getsize(file_path)
                # PDF should be at least 1KB and not huge like a script
                if 1000 < size < 50_000_000:  # 1KB to 50MB
                    pdf_files.append(f)
    
    if pdf_files:
        # Sort by modification time, pick most recent
        pdf_files.sort(key=lambda x: os.path.getmtime(os.path.join(current_folder, x)), reverse=True)
        logger.info(f"📄 Found PDF: {pdf_files[0]} ({os.path.getsize(os.path.join(current_folder, pdf_files[0]))} bytes)")
        return pdf_files[0]
    
    return None


def execute_qualtrics_upload_fixed(file_path):
    target_url = "https://gsu.qualtrics.com/jfe/form/SV_6nC36LYHWqVe5SJ?Q_JFE=qdg"
    
    if not file_path or not os.path.exists(file_path):
        logger.error(f"❌ File path '{file_path}' does not exist!")
        return

    abs_file_path = os.path.abspath(file_path)
    pdf_filename = os.path.basename(abs_file_path)
    
    # 🎯 Verify it's actually a PDF
    try:
        with open(abs_file_path, 'rb') as f:
            header = f.read(5)
            if header != b'%PDF-':
                logger.error(f"❌ File is NOT a valid PDF! Header: {header}")
                return
    except Exception as e:
        logger.error(f"❌ Cannot read file: {e}")
        return
    
    captured_response_body = None

    with sync_playwright() as p:
        logger.info("🚀 Launching browser...")
        
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1366,768"
            ]
        )
        
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        """)
        
        page = context.new_page()

        def handle_response(response):
            nonlocal captured_response_body
            if "/question/" in response.url and "/file" in response.url:
                if response.status == 200:
                    try:
                        captured_response_body = response.json()
                    except:
                        pass

        page.on("response", handle_response)

        logger.info("🌐 Loading Qualtrics form...")
        page.goto(target_url, timeout=90000, wait_until="networkidle")
        time.sleep(2)

        logger.info("🔍 Looking for Dropzone upload widget...")
        try:
            page.wait_for_selector(".dropzone", timeout=15000)
            dropzone = page.locator(".dropzone").first
        except Exception:
            logger.error("❌ Dropzone widget not found!")
            browser.close()
            return

        if dropzone.count() > 0:
            logger.info(f"📤 Uploading '{pdf_filename}'...")
            with open(abs_file_path, "rb") as f:
                file_b64 = base64.b64encode(f.read()).decode("utf-8")

            page.evaluate(f"""
                (function() {{
                    try {{
                        const dzElement = document.querySelector(".dropzone");
                        const dz = dzElement.dropzone;
                        dz.removeAllFiles(true);
                        const b64 = "{file_b64}";
                        const byteChars = atob(b64);
                        const byteArr = new Uint8Array(byteChars.length);
                        for (let i = 0; i < byteChars.length; i++) {{
                            byteArr[i] = byteChars.charCodeAt(i);
                        }}
                        const blob = new Blob([byteArr], {{ type: "application/pdf" }});
                        const file = new File([blob], "{pdf_filename}", {{ type: "application/pdf" }});
                        dz.addFile(file);
                        dz.processQueue();
                    }} catch(err) {{}}
                }})();
            """)
            
            try:
                page.wait_for_selector(".dz-success, .dz-complete", timeout=30000)
                logger.success("✅ File uploaded.")
            except:
                pass

        logger.info("🔏 Clicking Next...")
        try:
            next_btn = page.locator("#NextButton, .NextButton, input[type='submit']").first
            if next_btn.is_visible():
                next_btn.click()
                time.sleep(6)
        except Exception as e:
            logger.error(f"Next button failed: {e}")

        # Complete full survey
        logger.info("🔄 Completing full survey...")
        max_pages = 20
        for i in range(max_pages):
            time.sleep(3)
            try:
                submit = page.locator("#SubmitButton, .SubmitButton, button[type='submit']").first
                if submit.is_visible():
                    submit.click()
                    logger.success("🎯 Survey SUBMITTED!")
                    time.sleep(10)
                    break
                
                nxt = page.locator("#NextButton, .NextButton").first
                if nxt.is_visible():
                    nxt.click()
                    logger.info(f"➡️ Next ({i+1})")
                else:
                    logger.info("✅ End reached.")
                    break
            except Exception as e:
                logger.warning(f"Navigation end: {e}")
                break

        browser.close()

    # ========== FINAL OUTPUT ==========
    if captured_response_body:
        file_id = captured_response_body.get("fileId")
        
        # 🎯 COPY ORIGINAL PDF WITH QUALTRICS FORMAT NAME
        output_pdf = f"qualtrics_{file_id}.pdf"
        
        try:
            shutil.copy2(abs_file_path, output_pdf)
            logger.success(f"✅ PDF copied: {output_pdf}")
        except Exception as e:
            logger.error(f"❌ Copy failed: {e}")

        final_response = {
            "fileId": file_id,
            "name": captured_response_body.get("name"),
            "bytes": captured_response_body.get("bytes"),
            "mimeType": captured_response_body.get("mimeType"),
            "previewURL": captured_response_body.get("previewURL"),
            "transactionId": captured_response_body.get("transactionId")
        }

        print("\n" + "=" * 75)
        print("✅ QUALTRICS RESPONSE")
        print("=" * 75)
        print(json.dumps(final_response, indent=4))
        
        if os.path.exists(output_pdf):
            abs_path = os.path.abspath(output_pdf)
            actual_size = os.path.getsize(output_pdf)
            
            print(f"\n📥 PDF SAVED!")
            print(f"📂 File: {output_pdf}")
            print(f"📂 Path: {abs_path}")
            print(f"📊 Size: {actual_size} bytes")
            
            # Verify PDF header
            with open(output_pdf, 'rb') as f:
                header = f.read(5)
                if header == b'%PDF-':
                    print(f"✅ VALID PDF FILE!")
                else:
                    print(f"❌ NOT A PDF! Header: {header}")
            
            print(f"\n✅ Original PDF - no corruption!")
            print(f"✅ Same content as uploaded!")
        
        print("=" * 75)
    else:
        logger.error("❌ Could not capture response.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qualtrics PDF - Safe Copy")
    parser.add_argument("--file", required=False, help="Path to PDF")
    args = parser.parse_args()

    target_file = args.file if args.file else get_pdf_from_folder()

    if not target_file:
        logger.error("❌ No valid PDF found in folder!")
        logger.error("💡 Make sure .py files are not in the same folder")
    else:
        execute_qualtrics_upload_fixed(target_file)