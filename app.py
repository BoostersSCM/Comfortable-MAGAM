import streamlit as st
import os
import time
import re
import base64
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import pdfplumber

# --- 세션 설정 및 드라이버 초기화 ---
def get_driver():
    options = Options()
    options.add_argument("--headless") # 서버용 화면 없음 모드
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    # PDF 인쇄를 위한 전용 설정
    settings = {
        "recentDestinations": [{"id": "Save as PDF", "origin": "local"}],
        "selectedDestinationId": "Save as PDF",
        "version": 2
    }
    options.add_experimental_option("prefs", {
        "printing.print_preview_sticky_settings.appState": str(settings),
        "savefile.default_directory": "/tmp"
    })
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

# --- PDF에서 정보 추출 (사용자님의 기존 로직 유지) ---
def extract_info_from_pdf_data(pdf_content):
    try:
        with pdfplumber.open(pdf_content) as pdf:
            text = pdf.pages[0].extract_text()
            
            # 회사명 및 날짜 추출 로직 (기존 정규식 활용)
            date_pattern = r'(\d{4})[년\s]*(\d{1,2})[월\s]*(\d{1,2})[일\s]*'
            matches = re.findall(date_pattern, text)
            date_str = f"{matches[0][0]}{matches[0][1].zfill(2)}{matches[0][2].zfill(2)}" if matches else datetime.today().strftime("%Y%m%d")
            
            # (임시) 회사명 추출 로직 - 기존 코드를 여기에 통합하세요.
            company_name = "추출된업체명" 
            return company_name, date_str
    except:
        return "Unknown", datetime.today().strftime("%Y%m%d")

# --- Streamlit UI 구성 ---
st.title("📑 세금계산서 PDF 변환 자동화")
st.markdown("HTML 파일을 업로드하면 **비밀번호 입력부터 PDF 저장**까지 자동으로 처리합니다.")

uploaded_files = st.file_uploader("HTML 파일들을 선택하세요", type="html", accept_multiple_files=True)
biz_num = st.text_input("사업자번호", value="1828801269")

if st.button("변환 시작") and uploaded_files:
    driver = get_driver()
    
    for uploaded_file in uploaded_files:
        with st.status(f"처리 중: {uploaded_file.name}...", expanded=True) as status:
            # 1. HTML 파일 임시 저장
            temp_html = f"/tmp/{uploaded_file.name}"
            with open(temp_html, "wb") as f:
                f.write(uploaded_file.getvalue())
            
            # 2. Selenium 제어
            driver.get(f"file://{temp_html}")
            wait = WebDriverWait(driver, 10)
            
            # 암호 입력 및 확인
            pw_input = wait.until(EC.presence_of_element_located((By.XPATH, '//input[@type="password"]')))
            pw_input.send_keys(biz_num)
            driver.find_element(By.XPATH, '//button[contains(text(), "확인")]').click()
            time.sleep(3) # 페이지 로딩 대기
            
            # 3. PDF 변환 (Chrome DevTools Protocol 사용)
            # Headless 모드에서는 window.print() 대신 이 명령어를 사용해야 합니다.
            print_options = {
                'landscape': False,
                'displayHeaderFooter': False,
                'printBackground': True,
                'preferCSSPageSize': True,
            }
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", print_options)
            pdf_bytes = base64.b64decode(pdf_data['data'])
            
            # 4. 파일명 최적화 및 다운로드 버튼 생성
            # (추출 로직을 통해 파일명 생성 후)
            final_name = f"세금계산서_{uploaded_file.name.split('.')[0]}.pdf"
            
            st.download_button(
                label=f"📥 {final_name} 다운로드",
                data=pdf_bytes,
                file_name=final_name,
                mime="application/pdf"
            )
            status.update(label=f"✅ {uploaded_file.name} 완료!", state="complete")
            
    driver.quit()
