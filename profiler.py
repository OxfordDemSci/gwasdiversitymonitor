import time
import numpy as np
import sys
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities


def get_options():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument("--remote-debugging-port=9222")
    return options


def get_min_max_mean(obj):
    min = str(round(np.min(obj), 2))
    max = str(round(np.max(obj), 2))
    mean = str(round(np.mean(obj), 2))
    return min, max, mean


def prof_obj(obj, ts, driver):
    element_present = EC.presence_of_element_located((By.ID, obj))
    WebDriverWait(driver, float("inf")).until(element_present)
    return time.perf_counter() - ts


def main(address, num_iterations=100):
    options = get_options()
    ser = Service("./chromedriver")
    caps = DesiredCapabilities().CHROME
    caps["pageLoadStrategy"] = "none"
    driver = webdriver.Chrome(desired_capabilities=caps,
                              service=ser,
                              options=options)
    driver.get(address)
    profile_dict = {'bubbleSVG': [],
                    'timeSeriesSVG': [],
                    'worldMapSVG': [],
                    'heatmapSVG': [],
                    'doughnutSVG': []}
    for _ in range(int(num_iterations)):
        for obj in profile_dict:
            profile_dict[obj].append(prof_obj(obj,
                                              time.perf_counter(),
                                              driver))
    for obj in profile_dict:
        min, max, mean = get_min_max_mean(profile_dict[obj])
        print(obj + ': av=' + mean + 's, min=' + min + 's, max= ' + max + 's.')
    driver.quit()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
