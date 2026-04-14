import re
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from typing import Dict, Optional


class GannFetcher:
    URL = "https://www.pivottrading.co.in/forex/forexGannSquare.php"

    def __init__(self, headless: bool = True):
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument('--headless')
            options.add_argument('--disable-gpu')
        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        self.wait = WebDriverWait(self.driver, 15)

    def get_levels(self, price: float) -> Optional[Dict]:
        """
        Input: price (float)
        Returns: {
            'input_price': float,
            'buy_at': float,
            'buy_targets': [T1, T2, T3, T4],
            'buy_sl': float,
            'sell_at': float,
            'sell_targets': [T1, T2, T3, T4],
            'sell_sl': float
        }
        """
        try:
            self.driver.get(self.URL)

            # Wait for input field (id="ltp")
            input_field = self.wait.until(
                EC.presence_of_element_located((By.ID, "ltp"))
            )
            input_field.clear()
            input_field.send_keys(f"{price:.5f}")

            # Click Calculate button
            calc_btn = self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//input[@type='submit']"))
            )
            calc_btn.click()

            # Wait for result elements to be populated
            self.wait.until(
                EC.presence_of_element_located((By.ID, "buyAt"))
            )
            time.sleep(2)

            # Extract directly from span IDs
            buy_at = float(self.driver.find_element(
                By.ID, "buyAt").text.strip())
            buy_targets_text = self.driver.find_element(
                By.ID, "buy").text.strip()
            buy_sl = float(self.driver.find_element(
                By.ID, "buyStoploss").text.strip())

            sell_at = float(self.driver.find_element(
                By.ID, "sellAt").text.strip())
            sell_targets_text = self.driver.find_element(
                By.ID, "sell").text.strip()
            sell_sl = float(self.driver.find_element(
                By.ID, "sellStoploss").text.strip())

            # Parse targets from "1.7077 --- 1.7117 --- 1.7157 --- 1.7198"
            buy_targets = [float(x.strip())
                           for x in re.findall(r'[\d.]+', buy_targets_text)]
            sell_targets = [float(x.strip())
                            for x in re.findall(r'[\d.]+', sell_targets_text)]

            return {
                'input_price': price,
                'buy_at': buy_at,
                'buy_targets': buy_targets,
                'buy_sl': buy_sl,
                'sell_at': sell_at,
                'sell_targets': sell_targets,
                'sell_sl': sell_sl,
            }

        except Exception as e:
            print(f"Fetch error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def close(self):
        self.driver.quit()


if __name__ == "__main__":
    # Test
    fetcher = GannFetcher(headless=False)
    levels = fetcher.get_levels(1.70146)

    if levels:
        print("\n" + "="*50)
        print("GANN LEVELS FETCHED SUCCESSFULLY")
        print("="*50)
        print(json.dumps(levels, indent=2))

        # Calculate T1.5
        buy_t15 = (levels['buy_targets'][0] + levels['buy_targets'][1]) / 2
        sell_t15 = (levels['sell_targets'][0] + levels['sell_targets'][1]) / 2

        print(f"\n{'='*50}")
        print("CALCULATED ENTRY LEVELS (T1.5)")
        print("="*50)
        print(f"Buy BO Entry (T1.5):  {buy_t15:.5f}")
        print(f"Buy BO SL (Sell T1):  {levels['sell_targets'][0]:.5f}")
        print(f"\nSell BO Entry (T1.5): {sell_t15:.5f}")
        print(f"Sell BO SL (Buy T1):  {levels['buy_targets'][0]:.5f}")
    else:
        print("\nFailed to fetch levels")

    fetcher.close()
