from playwright.sync_api import sync_playwright
import time
import json

def run_test():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
            ]
        )
        context = browser.new_context(permissions=["microphone"])
        page = context.new_page()
        
        # Auto-dismiss alerts
        page.on("dialog", lambda dialog: (print(f"[DIALOG ALERT] {dialog.message}"), dialog.dismiss()))
        
        frontend_logs = []
        page.on("console", lambda msg: frontend_logs.append(f"[{msg.type}] {msg.text}"))
        
        print("Navigating to localhost:8000...")
        page.goto("http://localhost:8000")
        
        print("Checking consent checkbox...")
        page.check("#consent-checkbox")
        
        # Wait a moment for mic permission handling
        time.sleep(1)
        
        try:
            res = page.evaluate("navigator.mediaDevices.getUserMedia({ audio: true }).then(() => 'success').catch(e => e.name + ': ' + e.message)")
            print("getUserMedia directly:", res)
        except Exception as eval_e:
            print("Failed to evaluate getUserMedia:", eval_e)
            
        print("Checking if Start Recording is enabled...")
        is_disabled = page.is_disabled("#start-btn")
        print(f"Start button disabled: {is_disabled}")
        
        if not is_disabled:
            print("Clicking Start Recording...")
            page.click("#start-btn")
            
            # Record for 5 seconds
            time.sleep(5)
            
            print("Injecting dummy transcript...")
            page.evaluate("document.getElementById('transcript-box').textContent = 'Doctor: Patient presents with fever and cough for 2 days. Prescribed Dolo 650 twice daily.'")
            
            print("Clicking Stop Recording...")
            page.click("#stop-btn")
            
            # Wait for SOAP note generation
            print("Waiting 15 seconds for SOAP generation...")
            time.sleep(15)
        
        print("\n=== FRONTEND CONSOLE LOGS ===")
        for log in frontend_logs:
            print(log)
            
        print("\n=== SOAP UI VALUES ===")
        print("Chief Complaint:", page.input_value("#chief_complaint"))
        print("History:", page.input_value("#history"))
        print("Diagnosis:", page.input_value("#diagnosis"))
        
        browser.close()

if __name__ == "__main__":
    run_test()
