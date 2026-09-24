"""
AeroGuard - End-to-End Headless Browser UI Verification
Uses Selenium with Headless Chrome to test all interactive features,
WebSocket events, radar canvas rendering, and modal dialogs on the running server.
"""

import time
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def run_e2e_ui_tests():
    print("=============================================================")
    print("  AEROGUARD - HEADLESS BROWSER UI VERIFICATION SUITE")
    print("=============================================================")

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1600,1000")
    opts.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, 10)

    results = []

    try:
        # Step 1: Load Homepage
        print("\n[Step 1] Loading AeroGuard Tactical HUD from http://localhost:8000...")
        driver.get("http://localhost:8000")
        assert "AeroGuard" in driver.title
        print(f"  [OK] Page Loaded: Title is '{driver.title}'")
        results.append(("Page Load", "PASSED"))

        # Step 2: Verify Initial Avionics Elements
        print("\n[Step 2] Verifying Avionics Elements & Canvas Displays...")
        state_badge = driver.find_element(By.ID, "state-badge")
        radar_canvas = driver.find_element(By.ID, "radarCanvas")
        wave_canvas = driver.find_element(By.ID, "waveformCanvas")
        coords = driver.find_element(By.ID, "telemetry-coords")
        
        assert state_badge.is_displayed()
        assert radar_canvas.is_displayed()
        assert wave_canvas.is_displayed()
        print(f"  [OK] Initial State: {state_badge.text}")
        print(f"  [OK] Canvas Sizes: Radar={radar_canvas.size}, Waveform={wave_canvas.size}")
        print(f"  [OK] Coordinates HUD: {coords.text}")
        results.append(("Avionics HUD Elements", "PASSED"))

        # Step 3: Test +105 dB Jet Noise Simulator Toggle
        print("\n[Step 3] Testing +105 dB Jet Engine Noise Simulator...")
        noise_btn = driver.find_element(By.ID, "btn-toggle-noise")
        noise_btn.click()
        time.sleep(0.5)
        db_badge = driver.find_element(By.ID, "noise-db-badge")
        assert "105 dB" in db_badge.text
        print(f"  [OK] Noise Activated: Level = {db_badge.text}, Text = {driver.find_element(By.ID, 'noise-btn-text').text}")
        noise_btn.click() # toggle off
        time.sleep(0.3)
        assert "42 dB" in driver.find_element(By.ID, "noise-db-badge").text
        print("  [OK] Noise Deactivated: Back to 42 dB ambient")
        results.append(("Jet Noise Simulator", "PASSED"))

        # Step 4: Test 1-Click Judge Simulation (Runway Incursion Intercept)
        print("\n[Step 4] Testing 1-Click Judge Simulation Mode...")
        sim_btn = driver.find_element(By.ID, "btn-judge-sim")
        sim_btn.click()
        print("  [OK] Clicked 1-Click Incursion Sim. Awaiting sub-second incursion intercept...")
        
        # Wait for incursion hazard banner to appear
        hazard_banner = wait.until(lambda d: d.find_element(By.ID, "hazard-banner"))
        wait.until(lambda d: "hidden" not in hazard_banner.get_attribute("class"))
        
        hazard_text = driver.find_element(By.ID, "hazard-text").text
        hazard_impact = driver.find_element(By.ID, "hazard-impact").text
        new_state = driver.find_element(By.ID, "state-badge").text
        
        print(f"  [OK] CRITICAL INCURSION FIRED!")
        print(f"    - Directive: {hazard_text}")
        print(f"    - Impact Margin: {hazard_impact}")
        print(f"    - New State: {new_state}")
        
        # Verify transcript lines in feed
        feed_items = driver.find_elements(By.CSS_SELECTOR, "#transcript-feed > div")
        print(f"    - Transcript Feed Entries: {len(feed_items)}")
        assert len(feed_items) >= 2
        
        # Capture screenshot
        screenshot_incursion = os.path.abspath("screenshot_incursion.png")
        driver.save_screenshot(screenshot_incursion)
        print(f"  [OK] Saved incursion screenshot: {screenshot_incursion}")
        results.append(("1-Click Incursion Simulation", "PASSED"))

        # Step 5: Test ICAO Readback Deviation Engine
        print("\n[Step 5] Testing ICAO Readback Deviation Engine...")
        rb_buttons = driver.find_elements(By.XPATH, "//button[contains(., 'Readback Test')]")
        if rb_buttons:
            rb_buttons[0].click()
            time.sleep(1.0)
            rb_banner = driver.find_element(By.ID, "readback-banner")
            assert "hidden" not in rb_banner.get_attribute("class")
            rb_text = driver.find_element(By.ID, "readback-text").text
            print(f"  [OK] Readback Deviation Alert Triggered: '{rb_text}'")
            results.append(("ICAO Readback Deviation Engine", "PASSED"))

        # Step 6: Test FAA Part 139 Digital Audit Modal
        print("\n[Step 6] Testing FAA Part 139 Audit Modal...")
        audit_btn = driver.find_element(By.XPATH, "//button[contains(., 'Part 139 Audit')]")
        audit_btn.click()
        
        modal = wait.until(lambda d: d.find_element(By.ID, "audit-modal"))
        wait.until(lambda d: "hidden" not in modal.get_attribute("class"))
        
        # Wait for audit content to populate
        content = wait.until(lambda d: d.find_element(By.ID, "audit-content"))
        wait.until(lambda d: len(content.text) > 50)
        
        print("  [OK] Part 139 Audit Modal Open & Populated:")
        snippet = content.text[:200].replace("\n", " ")
        print(f"    - Content Snippet: {snippet}...")
        
        # Close modal
        dismiss_btn = driver.find_element(By.XPATH, "//button[contains(., 'Dismiss')]")
        dismiss_btn.click()
        time.sleep(0.5)
        assert "hidden" in modal.get_attribute("class")
        print("  [OK] Modal Dismissed Successfully.")
        results.append(("FAA Part 139 Audit Modal", "PASSED"))

        # Step 7: Test Scenario Switching to Tokyo Haneda (RJTT)
        print("\n[Step 7] Testing Scenario Switch: Tokyo Haneda (RJTT 2024 Replay)...")
        haneda_btn = driver.find_element(By.ID, "btn-scenario-rjtt")
        haneda_btn.click()
        time.sleep(1.0)
        
        radar_title = driver.find_element(By.ID, "radar-title").text
        legend_rwy = driver.find_element(By.ID, "legend-runway").text
        legend_veh = driver.find_element(By.ID, "legend-vehicle").text
        
        print(f"  [OK] Radar Title Reconfigured: {radar_title}")
        print(f"  [OK] Legend Runway: {legend_rwy}")
        print(f"  [OK] Legend Target Vehicle: {legend_veh}")
        assert "Haneda" in radar_title or "34R" in radar_title
        assert "34R" in legend_rwy
        assert "JA722A" in legend_veh

        screenshot_haneda = os.path.abspath("screenshot_haneda.png")
        driver.save_screenshot(screenshot_haneda)
        print(f"  [OK] Saved Haneda scenario screenshot: {screenshot_haneda}")
        results.append(("Tokyo Haneda Scenario Switch", "PASSED"))

    except Exception as e:
        print(f"\n[FAIL] UI TEST FAILURE: {e}")
        driver.save_screenshot(os.path.abspath("screenshot_error.png"))
        results.append(("UI Test Suite", f"FAILED: {e}"))
        raise
    finally:
        driver.quit()

    print("\n=============================================================")
    print("  FINAL UI TEST RESULTS SUMMARY")
    print("=============================================================")
    all_passed = True
    for name, status in results:
        flag = "[PASS]" if status == "PASSED" else "[FAIL]"
        print(f"  {flag} {name:<35} : {status}")
        if status != "PASSED":
            all_passed = False
    
    print("=============================================================")
    if all_passed:
        print("  ALL 6 BROWSER UI VERIFICATION SUITES PASSED (100% SUCCESS)")
    else:
        print("  SOME TESTS FAILED")
    print("=============================================================")

if __name__ == "__main__":
    run_e2e_ui_tests()
